"""
Implements PlatformAdapter for YouTube.

For local development, media_s3_key is treated as a local file path
(e.g. /app/media/test_clip.mp4). Once S3 is wired up, this adapter can
download the object to a temporary local path without changing the
publish() interface.

YouTube-specific metadata handled here:
- title
- description
- native YouTube tags
- default language
- custom thumbnail
- privacy status
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

RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

MAX_UPLOAD_ATTEMPTS = 3
BACKOFF_SECONDS = [2, 8, 30]

# Reasons Google uses specifically for quota/rate-limit exhaustion.
QUOTA_EXCEEDED_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "rateLimitExceeded",
}


def _is_retryable_http_error(e: HttpError) -> bool:
    """
    5xx errors are retryable.

    4xx errors are not retried because they generally indicate a client
    problem such as invalid metadata, invalid authentication, forbidden
    access, quota exhaustion, etc.
    """
    status = getattr(e.resp, "status", None)
    return status is not None and 500 <= status < 600


def _extract_quota_reason(e: HttpError) -> str | None:
    """
    Extract the specific quota reason from a YouTube API error response.

    Returns None if the response is not valid JSON or does not contain
    one of the known quota-related reasons.
    """
    try:
        body = json.loads(
            e.content.decode("utf-8")
        )

        errors = body.get(
            "error",
            {},
        ).get(
            "errors",
            [],
        )

        for error in errors:
            reason = error.get("reason")

            if reason in QUOTA_EXCEEDED_REASONS:
                return reason

    except (
        ValueError,
        AttributeError,
        UnicodeDecodeError,
    ):
        pass

    return None


def _thumbnail_mime_type(path: str) -> str:
    """
    Resolve the MIME type for a supported thumbnail file.
    """
    extension = os.path.splitext(path)[1].lower()

    if extension == ".png":
        return "image/png"

    return "image/jpeg"


class YouTubeAdapter(PlatformAdapter):

    def _get_credentials(
        self,
        account: SocialAccount,
    ) -> Credentials:
        """
        Load OAuth credentials from the configured secrets store.
        """
        secret = secrets_service.get_secret(
            account.secrets_manager_arn
        )

        return Credentials(
            token=secret["token"],
            refresh_token=secret["refresh_token"],
            token_uri=secret["token_uri"],
            client_id=secret["client_id"],
            client_secret=secret["client_secret"],
            scopes=secret["scopes"],
        )

    async def refresh_token_if_needed(
        self,
        account: SocialAccount,
    ) -> None:
        """
        Refresh an expired access token and persist the refreshed
        credentials back to the secrets store.
        """
        creds = self._get_credentials(account)

        if creds.expired and creds.refresh_token:
            creds.refresh(
                GoogleAuthRequest()
            )

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

    def _upload_blocking(
        self,
        local_path: str,
        credentials: Credentials,
        target: PostTarget,
    ) -> dict:
        """
        Perform the blocking YouTube upload.

        The actual video upload happens first. Once YouTube returns a
        video ID, a custom thumbnail is applied separately.
        """
        youtube = build(
            "youtube",
            "v3",
            credentials=credentials,
        )

        snippet = {
            "title": (
                target.effective_title
                or "Untitled TV9 upload"
            ),
            "description": (
                target.effective_caption
                or ""
            ),
            "categoryId": (
                target.effective_youtube_category_id
                or "25"
            ),
        }

        # Native YouTube tags.
        #
        # These are separate from hashtags appearing in the description.
        tags = target.effective_youtube_tags

        if tags:
            snippet["tags"] = tags

        # Apply the language associated with this target's title and
        # description.
        if target.language:
            snippet["defaultLanguage"] = target.language

        body = {
            "snippet": snippet,
            "status": {
                "privacyStatus": (
                    target.effective_youtube_privacy_status
                    or "private"
                ),
            },
        }

        media = MediaFileUpload(
            local_path,
            chunksize=-1,
            resumable=True,
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None

        while response is None:
            _, response = request.next_chunk()

        video_id = response.get("id")

        if not video_id:
            raise PublishError(
                "YouTube upload completed but returned no video ID"
            )

        # ---------------------------------------------------------
        # Custom thumbnail
        # ---------------------------------------------------------
        thumbnail_path = target.effective_thumbnail_s3_key

        if thumbnail_path:
            if not os.path.exists(thumbnail_path):
                logger.warning(
                    "Custom thumbnail was configured but the file "
                    "was not found at %s. Video %s will keep the "
                    "YouTube-generated thumbnail.",
                    thumbnail_path,
                    video_id,
                )
            else:
                try:
                    thumbnail_media = MediaFileUpload(
                        thumbnail_path,
                        mimetype=_thumbnail_mime_type(
                            thumbnail_path
                        ),
                        resumable=False,
                    )

                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=thumbnail_media,
                    ).execute()

                    logger.info(
                        "Custom thumbnail applied to YouTube video %s",
                        video_id,
                    )

                except HttpError as e:
                    # The video itself has already been uploaded.
                    #
                    # Do not treat a thumbnail-only failure as a complete
                    # publish failure, otherwise retrying could upload the
                    # same video again.
                    logger.warning(
                        "Video %s uploaded, but the custom thumbnail "
                        "could not be applied: %s",
                        video_id,
                        e,
                    )

        return response

    async def _upload_with_retry(
        self,
        local_path: str,
        credentials: Credentials,
        target: PostTarget,
    ) -> dict:
        """
        Run the blocking YouTube upload in a worker thread and retry
        transient failures.
        """
        last_exception: Exception | None = None

        for attempt in range(
            1,
            MAX_UPLOAD_ATTEMPTS + 1,
        ):
            try:
                return await asyncio.to_thread(
                    self._upload_blocking,
                    local_path,
                    credentials,
                    target,
                )

            except HttpError as e:
                status = getattr(
                    e.resp,
                    "status",
                    "unknown",
                )

                if not _is_retryable_http_error(e):
                    quota_reason = _extract_quota_reason(e)

                    if quota_reason:
                        logger.warning(
                            "YouTube upload failed due to quota "
                            "(%s) on attempt %d — not retrying",
                            quota_reason,
                            attempt,
                        )
                    else:
                        logger.warning(
                            "YouTube upload failed with "
                            "non-retryable status %s on attempt %d "
                            "— not retrying",
                            status,
                            attempt,
                        )

                    raise

                last_exception = e

                logger.warning(
                    "YouTube upload failed with retryable status "
                    "%s on attempt %d/%d",
                    status,
                    attempt,
                    MAX_UPLOAD_ATTEMPTS,
                )

            except RETRYABLE_EXCEPTIONS as e:
                last_exception = e

                logger.warning(
                    "YouTube upload failed with %s on attempt %d/%d",
                    type(e).__name__,
                    attempt,
                    MAX_UPLOAD_ATTEMPTS,
                )

            if attempt < MAX_UPLOAD_ATTEMPTS:
                delay = BACKOFF_SECONDS[
                    attempt - 1
                ]

                logger.info(
                    "Retrying YouTube upload in %ds "
                    "(attempt %d/%d next)",
                    delay,
                    attempt + 1,
                    MAX_UPLOAD_ATTEMPTS,
                )

                await asyncio.sleep(delay)

        if last_exception:
            raise last_exception

        raise PublishError(
            "YouTube upload failed without a specific error"
        )

    async def publish(
        self,
        target: PostTarget,
        account: SocialAccount,
    ) -> PublishResult:
        """
        Publish a PostTarget to YouTube.
        """
        local_path = target.effective_media_s3_key

        if not local_path:
            raise PublishError(
                "Post has no media file — YouTube requires a video file"
            )

        if not os.path.exists(local_path):
            raise PublishError(
                f"Video file not found at {local_path}"
            )

        try:
            await self.refresh_token_if_needed(
                account
            )

            credentials = self._get_credentials(
                account
            )

            response = await self._upload_with_retry(
                local_path,
                credentials,
                target,
            )

            return PublishResult(
                platform_post_id=response["id"]
            )

        except HttpError as e:
            quota_reason = _extract_quota_reason(e)

            if quota_reason:
                raise PublishError(
                    "YouTube daily quota exceeded "
                    f"({quota_reason}) — check quota usage "
                    "in Google Cloud Console"
                ) from e

            status = getattr(
                e.resp,
                "status",
                "unknown",
            )

            raise PublishError(
                "YouTube upload failed after retries "
                f"(HTTP {status}): {e}"
            ) from e

        except PublishError:
            raise

        except Exception as e:
            raise PublishError(
                f"YouTube upload failed: "
                f"{type(e).__name__}: {e}"
            ) from e