"""
Implements PlatformAdapter for YouTube. For local dev, `post.media_s3_key`
is treated as a local file path (e.g. /app/media/test_clip.mp4) mounted
into the container. Once S3 is wired up in the AWS phase, this will
download the object from S3 to a temp path first — the publish()
signature won't need to change.
"""

import asyncio
import os

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.adapters.base import PlatformAdapter, PublishResult, PublishError
from app.models import PostTarget, SocialAccount
from app.services import secrets_service


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
        """
        The actual upload. The Google API client's upload calls
        (request.next_chunk()) are synchronous/blocking — there's no
        native async version. Running this via asyncio.to_thread (see
        publish() below) is what lets several of these run at the same
        time (e.g. uploading to 3 different YouTube channels at once)
        without one upload blocking the others on the same event loop.

        Uses target.effective_title / target.effective_caption rather
        than the Post's title/caption directly — this is what makes a
        Hindi-language target actually publish with the Hindi title,
        instead of every channel getting the same default text
        regardless of what language override was set for it.
        """
        youtube = build("youtube", "v3", credentials=credentials)

        body = {
            "snippet": {
                "title": target.effective_title or "Untitled TV9 upload",
                "description": target.effective_caption or "",
                "categoryId": "25",  # News & Politics
            },
            "status": {
                "privacyStatus": "private",  # keep uploads private during testing — change once trusted
            },
        }

        media = MediaFileUpload(local_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
        return response

    async def publish(self, target: PostTarget, account: SocialAccount) -> PublishResult:
        local_path = target.effective_media_s3_key
        if not local_path:
            raise PublishError("Post has no media file — YouTube requires a video file")

        if not os.path.exists(local_path):
            raise PublishError(f"Video file not found at {local_path}")

        try:
            await self.refresh_token_if_needed(account)
            creds = self._get_credentials(account)
            response = await asyncio.to_thread(self._upload_blocking, local_path, creds, target)
            return PublishResult(platform_post_id=response["id"])
        except Exception as e:  # noqa: BLE001 — surfacing any upload failure as PublishError
            raise PublishError(f"YouTube upload failed: {type(e).__name__}: {e}") from e