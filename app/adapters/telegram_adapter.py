"""
Implements PlatformAdapter for Telegram. Unlike YouTube, there's no
OAuth flow — a bot token (created once via @BotFather) can post to any
channel/group it's been added to as an admin. refresh_token_if_needed
is a deliberate no-op because bot tokens don't expire.

Telegram posts here are text-only: title + caption + a link back to
the corresponding YouTube upload on the same Post, rather than a
direct video upload. This avoids Telegram Bot API's
direct-upload
limit entirely and matches the pattern already used in the legacy
rsspostbangla.py script (title + link, sent as HTML-formatted text).

Because the link comes from a SIBLING PostTarget's result (the YouTube
target on the same Post), this adapter reads target.post.targets to
find that sibling — which only works correctly if the YouTube target
has already published by the time this runs. See the two-phase
ordering in posts.py's publish_all and scheduler.py for how that's
guaranteed.
"""

import asyncio
import html

import requests

from app.adapters.base import PlatformAdapter, PublishResult, PublishError
from app.models import PostTarget, SocialAccount
from app.services import secrets_service

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 20


class TelegramAdapter(PlatformAdapter):
    async def refresh_token_if_needed(self, account: SocialAccount) -> None:
        # Bot tokens don't expire — nothing to refresh. Present only to
        # satisfy the PlatformAdapter interface.
        return None

    def _find_youtube_link(self, target: PostTarget) -> str | None:
        """
        Looks for a sibling PostTarget on the same Post that's a
        published YouTube upload, and builds a youtu.be link from its
        platform_post_id (YouTube's video ID). Returns None if no
        YouTube sibling exists yet, or it hasn't published successfully
        — callers should treat that as "link not available" rather
        than an error, since a post might legitimately go to Telegram
        without ever targeting YouTube.
        """
        post = target.post
        if not post:
            return None
        for sibling in post.targets:
            if (
                sibling.social_account
                and sibling.social_account.platform == "youtube"
                and sibling.status == "published"
                and sibling.platform_post_id
            ):
                return f"https://youtu.be/{sibling.platform_post_id}"
        return None

    def _compose_message(self, target: PostTarget) -> str:
        title = target.effective_title or "Untitled TV9 upload"
        caption = target.effective_caption or ""
        link = self._find_youtube_link(target)

        parts = [f"<b>{html.escape(title)}</b>"]
        if caption:
            parts.append(html.escape(caption))
        if link:
            parts.append(link)
        # If there's genuinely no YouTube sibling on this post at all
        # (Telegram-only post), that's fine — just title + caption,
        # no missing-link placeholder needed.

        return "\n\n".join(parts)

    def _send_blocking(self, bot_token: str, chat_id: str, text: str) -> dict:
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        response = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        data = response.json()
        if not data.get("ok"):
            raise PublishError(f"Telegram API error: {data.get('description', 'unknown error')}")
        return data["result"]

    async def publish(self, target: PostTarget, account: SocialAccount) -> PublishResult:
        secret = secrets_service.get_secret(account.secrets_manager_arn)
        bot_token = secret.get("bot_token")
        chat_id = secret.get("chat_id")
        if not bot_token or not chat_id:
            raise PublishError(
                "Telegram account is missing bot_token or chat_id in its stored secret"
            )

        text = self._compose_message(target)

        try:
            result = await asyncio.to_thread(self._send_blocking, bot_token, chat_id, text)
            return PublishResult(platform_post_id=str(result["message_id"]))
        except PublishError:
            raise
        except requests.RequestException as e:
            raise PublishError(f"Telegram request failed: {type(e).__name__}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise PublishError(f"Telegram publish failed unexpectedly: {type(e).__name__}: {e}") from e