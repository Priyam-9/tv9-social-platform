"""
Implements PlatformAdapter for YouTube. For local dev, `post.media_s3_key`
is treated as a local file path (e.g. /app/media/test_clip.mp4) mounted
into the container. Once S3 is wired up in the AWS phase, this will
download the object from S3 to a temp path first — the publish()
signature won't need to change.
"""

import asyncio
import json
import logging
import os

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.adapters.base import PlatformAdapter, PublishResult, PublishError
from app.models import PostTarget, SocialAccount
from app.services import secrets_service

logger = logging.getLogger("tv9.youtube")

RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)
MAX_UPLOAD_ATTEMPTS = 3
BACKOFF_SECONDS = [2, 8, 30]

# Reasons Google uses in the error body specifically for quota
# exhaustion — distinct from other 403s like insufficient permissions
# or channel not found, which should keep their normal generic message.
QUOTA_EXCEEDED_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


def _is_retryable_http_error(e: HttpError) -> bool:
    """
    5xx (server-side, e.g. YouTube having a bad moment) is retryable.
    4xx (client-side, e.g. quota exceeded, bad request, invalid auth)
    is NOT — retrying a 4xx just repeats the same failure and, in the
    quota-exceeded case specifically, burns more of an already-scarce
    daily quota for no benefit.
    """
    status = getattr(e.resp, "status", None)
    return status is not None and 500 <= status < 600


def _extract_quota_reason(e: HttpError) -> str | None:
    """
    Returns the specific quota-related reason string if this HttpError
    is a quota/rate-limit failure, or None otherwise. Google's error
    body looks like:
      {"error": {"errors": [{"reason": "quotaExceeded", ...}], ...}}
    Parsed defensively — if the body isn't the shape we expect (e.g.
    a non-JSON error page from an intermediate proxy), this just
    returns None and the caller falls back to the generic error path
    rather than raising a second, confusing exception.
    """
    try:
        body = json.loads(e.content.decode("utf-8"))
        errors = body.get("error", {}).get("errors", [])
        for err in errors:
            reason = err.get("reason")
            if reason in QUOTA_EXCEEDED_REASONS:
                return reason
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    return None


class YouTubeAdapter(PlatformAdapter):
    def _get_credentials(self, account: SocialAccount) -> Credentials:
        secret = secrets_service.get_secret(account.secrets_manager_arn)
        creds = Credentials(
            token=secret["token"],
            refresh_token=secret["refresh_token"],
            token_uri=secret["token_uri"],
            client_id=secret["client_id"],
            client_secret=secret["client_secret"],
            scopes=secret["scopes"],
        )
        return creds

    async def refresh_token_if_needed(self, account: SocialAccount) -> None:
        creds = self._get_credentials(account)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            secrets_service.save_secret(
                account.secrets_manager_arn,
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                },
            )

    def _upload_blocking(self, local_path: str, credentials: Credentials, target: PostTarget) -> dict:
        youtube = build("youtube", "v3", credentials=credentials)

        body = {
            "snippet": {
                "title": target.effective_title or "Untitled TV9 upload",
                "description": target.effective_caption or "",
                "categoryId": "25",  # News & Politics
            },
            "status": {
                "privacyStatus": "private",
            },
        }

        media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
        return response

    async def _upload_with_retry(self, local_path: str, credentials: Credentials, target: PostTarget) -> dict:
        last_exception: Exception | None = None

        for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
            try:
                return await asyncio.to_thread(self._upload_blocking, local_path, credentials, target)
            except HttpError as e:
                status = getattr(e.resp, "status", "unknown")
                if not _is_retryable_http_error(e):
                    quota_reason = _extract_quota_reason(e)
                    if quota_reason:
                        logger.warning(
                            "YouTube upload failed due to quota (%s) on attempt %d — not retrying",
                            quota_reason, attempt,
                        )
                    else:
                        logger.warning(
                            "YouTube upload failed with non-retryable status %s on attempt %d — not retrying",
                            status, attempt,
                        )
                    raise
                last_exception = e
                logger.warning(
                    "YouTube upload failed with retryable status %s on attempt %d/%d",
                    status, attempt, MAX_UPLOAD_ATTEMPTS,
                )
            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e
                logger.warning(
                    "YouTube upload failed with %s on attempt %d/%d",
                    type(e).__name__, attempt, MAX_UPLOAD_ATTEMPTS,
                )

            if attempt < MAX_UPLOAD_ATTEMPTS:
                delay = BACKOFF_SECONDS[attempt - 1]
                logger.info("Retrying YouTube upload in %ds (attempt %d/%d next)", delay, attempt + 1, MAX_UPLOAD_ATTEMPTS)
                await asyncio.sleep(delay)

        raise last_exception

    async def publish(self, target: PostTarget, account: SocialAccount) -> PublishResult:
        local_path = target.effective_media_s3_key
        if not local_path:
            raise PublishError("Post has no media file — YouTube requires a video file")

        if not os.path.exists(local_path):
            raise PublishError(f"Video file not found at {local_path}")

        try:
            await self.refresh_token_if_needed(account)
            creds = self._get_credentials(account)
            response = await self._upload_with_retry(local_path, creds, target)
            return PublishResult(platform_post_id=response["id"])
        except HttpError as e:
            quota_reason = _extract_quota_reason(e)
            if quota_reason:
                # Distinct, actionable message — tells you immediately
                # this is a quota problem, not a broken video file or
                # bad auth, so you know to check the Cloud Console
                # instead of debugging the upload itself.
                raise PublishError(
                    f"YouTube daily quota exceeded ({quota_reason}) — check quota usage in Google Cloud Console"
                ) from e
            status = getattr(e.resp, "status", "unknown")
            raise PublishError(f"YouTube upload failed after retries (HTTP {status}): {e}") from e
        except Exception as e:  # noqa: BLE001
            raise PublishError(f"YouTube upload failed: {type(e).__name__}: {e}") from e