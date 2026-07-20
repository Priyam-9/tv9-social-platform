"""
Implements PlatformAdapter for YouTube. For local dev, `post.media_s3_key`
is treated as a local file path (e.g. /app/media/test_clip.mp4) mounted
into the container. Once S3 is wired up in the AWS phase, this will
download the object from S3 to a temp path first — the publish()
signature won't need to change.
"""

import os

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.adapters.base import PlatformAdapter, PublishResult, PublishError
from app.models import Post, SocialAccount
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

    async def publish(self, post: Post, account: SocialAccount) -> PublishResult:
        if not post.media_s3_key:
            raise PublishError("Post has no media file — YouTube requires a video file")

        local_path = post.media_s3_key  # local dev: treated as a direct file path
        if not os.path.exists(local_path):
            raise PublishError(f"Video file not found at {local_path}")

        await self.refresh_token_if_needed(account)
        creds = self._get_credentials(account)
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": post.title or "Untitled TV9 upload",
                "description": post.caption or "",
                "categoryId": "25",  # News & Politics
            },
            "status": {
                "privacyStatus": "private",  # keep uploads private during testing — change once trusted
            },
        }

        media = MediaFileUpload(local_path, chunksize=-1, resumable=True)

        try:
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                status, response = request.next_chunk()
            return PublishResult(platform_post_id=response["id"])
        except Exception as e:  # noqa: BLE001 — surfacing any upload failure as PublishError
            raise PublishError(f"YouTube upload failed: {e}") from e
