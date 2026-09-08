"""
Implements PlatformAdapter for Telegram. Unlike YouTube, there's no
OAuth flow — a bot token (created once via @BotFather) can post to any
channel/group it's been added to as an admin. refresh_token_if_needed
is a deliberate no-op because bot tokens don't expire.

Telegram posts here MIRROR every YouTube sibling target's actual
title/caption (whatever language override YouTube used) plus a link
back to that upload — one Telegram message PER YouTube target, so a
post going out to e.g. a Hindi channel and a Marathi channel produces
two separate Telegram messages, each with its own title/caption/link
and its own language label if one was set.

FAILED-SIBLING BEHAVIOR: if a YouTube sibling target did NOT publish
successfully, its Telegram message is skipped entirely — not sent
with a missing link. A Telegram message with a title/caption but no
video attached looks like a working post with a broken link, which is
worse than no message at all; skipping makes the failure visible via
absence rather than papering over it with half-working content.

This is text-only — no direct video upload — which avoids Telegram
Bot API's 50MB direct-upload limit entirely, matching the pattern
already used in the legacy rsspostbangla.py script (title + link,
sent as HTML-formatted text).

Because the mirrored text and links come from SIBLING PostTargets'
results, this adapter reads target.post.targets to find them — which
only works correctly if the YouTube targets have already published (or
failed) by the time this runs. See the two-phase ordering in posts.py's
publish_all and scheduler.py for how that's guaranteed.
"""

import asyncio
import html
import logging

import requests

from app.adapters.base import PlatformAdapter, PublishResult, PublishError
from app.models import PostTarget, SocialAccount
from app.services import secrets_service

logger = logging.getLogger("tv9.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 20


class TelegramAdapter(PlatformAdapter):
    async def refresh_token_if_needed(self, account: SocialAccount) -> None:
        return None

    def _find_youtube_siblings(self, target: PostTarget) -> list[PostTarget]:
        post = target.post
        if not post:
            return []
        return [
            sibling
            for sibling in post.targets
            if sibling.social_account and sibling.social_account.platform == "youtube"
        ]

    def _compose_messages(self, target: PostTarget) -> list[str]:
        """
        Returns a list of message texts to send — one per YouTube
        sibling that published successfully. Siblings that failed are
        skipped entirely (see module docstring). If the post has no
        YouTube target at all, falls back to a single message using
        Telegram's own title/caption fields, unchanged from before.
        """
        youtube_siblings = self._find_youtube_siblings(target)

        if not youtube_siblings:
            title = target.effective_title or "Untitled TV9 upload"
            caption = target.effective_caption or ""
            parts = [f"<b>{html.escape(title)}</b>"]
            if caption:
                parts.append(html.escape(caption))
            return ["\n\n".join(parts)]

        messages = []
        for sibling in youtube_siblings:
            if sibling.status != "published" or not sibling.platform_post_id:
                logger.info(
                    "Skipping Telegram message for YouTube sibling %s (status=%s) — not published, no message sent",
                    sibling.id, sibling.status,
                )
                continue

            title = sibling.effective_title or "Untitled TV9 upload"
            caption = sibling.effective_caption or ""
            link = f"https://youtu.be/{sibling.platform_post_id}"
            label = f"[{sibling.language.upper()}] " if sibling.language else ""

            parts = [f"<b>{label}{html.escape(title)}</b>"]
            if caption:
                parts.append(html.escape(caption))
            parts.append(link)

            messages.append("\n\n".join(parts))

        return messages

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

        messages = self._compose_messages(target)

        if not messages:
            # Every YouTube sibling failed — nothing to send. This is
            # a real failure, not a silent no-op: the caller should
            # see this target as failed, not "published" with zero
            # actual messages sent.
            raise PublishError(
                "No Telegram message sent — all YouTube sibling targets failed to publish"
            )

        try:
            message_ids = []
            for text in messages:
                result = await asyncio.to_thread(self._send_blocking, bot_token, chat_id, text)
                message_ids.append(str(result["message_id"]))

            return PublishResult(platform_post_id=",".join(message_ids))
        except PublishError:
            raise
        except requests.RequestException as e:
            raise PublishError(f"Telegram request failed: {type(e).__name__}: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise PublishError(f"Telegram publish failed unexpectedly: {type(e).__name__}: {e}") from e