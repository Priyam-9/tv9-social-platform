"""
Implements PlatformAdapter for Telegram. Unlike YouTube, there's no
OAuth flow — a bot token (created once via @BotFather) can post to any
channel/group it's been added to as an admin. refresh_token_if_needed
is a deliberate no-op because bot tokens don't expire.

Telegram posts here MIRROR the YouTube sibling target's actual
title/caption (whatever language override YouTube used) plus a link
back to that upload — rather than using Telegram's own separate
title/caption fields. This means you never have to retype or
duplicate the same text for Telegram; it automatically matches
whatever YouTube actually published, in whatever language that was.
Telegram's own title/caption fields (if filled in) are only used as a
fallback when the post has no YouTube target at all.

This is text-only — no direct video upload — which avoids Telegram
Bot API's 50MB direct-upload limit entirely, matching the pattern
already used in the legacy rsspostbangla.py script (title + link,
sent as HTML-formatted text).

Because the mirrored text and link come from a SIBLING PostTarget's
result, this adapter reads target.post.targets to find it — which
only works correctly if the YouTube target has already published by
the time this runs. See the two-phase ordering in posts.py's
publish_all and scheduler.py for how that's guaranteed.
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

    def _find_youtube_sibling(self, target: PostTarget) -> PostTarget | None:
        """
        Returns the first YouTube PostTarget on the same Post, if any
        — regardless of whether it's published yet. Used both to
        mirror its title/caption and (once published) to build the
        link. If a post has more than one YouTube target (e.g. a
        Hindi channel and a Marathi channel), this picks whichever one
        appears first — there's no per-post way to choose which one
        Telegram mirrors yet.
        """
        post = target.post
        if not post:
            return None
        for sibling in post.targets:
            if sibling.social_account and sibling.social_account.platform == "youtube":
                return sibling
        return None

    def _compose_message(self, target: PostTarget) -> str:
        youtube_sibling = self._find_youtube_sibling(target)

        if youtube_sibling:
            # Mirror YouTube's ACTUAL published text, not Telegram's
            # own fields — this is what makes a Hindi-override YouTube
            # upload produce a Hindi Telegram message automatically,
            # without retyping anything into Telegram's own caption.
            title = youtube_sibling.effective_title or "Untitled TV9 upload"
            caption = youtube_sibling.effective_caption or ""
        else:
            # No YouTube target on this post at all — fall back to
            # Telegram's own title/caption so Telegram-only posts
            # still work.
            title = target.effective_title or "Untitled TV9 upload"
            caption = target.effective_caption or ""

        link = None
        if (
            youtube_sibling
            and youtube_sibling.status == "published"
            and youtube_sibling.platform_post_id
        ):
            link = f"https://youtu.be/{youtube_sibling.platform_post_id}"

        parts = [f"<b>{html.escape(title)}</b>"]
        if caption:
            parts.append(html.escape(caption))
        if link:
            parts.append(link)
        # If there's a YouTube sibling but it hasn't published yet
        # (shouldn't normally happen given the two-phase ordering),
        # this just omits the link rather than erroring — better to
        # send the text without a link than to fail the whole post.

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