from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import PostTarget, SocialAccount


@dataclass
class PublishResult:
    platform_post_id: str


class PublishError(Exception):
    """Raised by an adapter when publishing fails. The worker catches
    this, records error_message on the PostTarget, and lets SQS retry."""


class PlatformAdapter(ABC):
    """
    Common interface every platform adapter implements. The worker
    service (built in the next phase) only ever talks to this
    interface — it never needs to know YouTube's quota rules or
    Instagram's container model directly.

    publish() takes the PostTarget rather than the Post itself. This is
    what lets an adapter publish per-target language overrides (a
    Hindi title/caption for the TV9 Hindi channel, a Telugu one for
    TV9 Telugu, etc.) instead of always publishing the Post's default
    text to every channel regardless of language. Use
    target.effective_title / target.effective_caption inside an
    adapter — those already fall back to the Post's default when a
    target has no override, so you don't need to duplicate that
    fallback logic in every adapter. The underlying Post is still
    reachable via target.post for anything that's genuinely
    post-level rather than per-target (e.g. media_s3_key, media_type).
    """

    @abstractmethod
    async def publish(self, target: PostTarget, account: SocialAccount) -> PublishResult:
        """Publish `target` (one post-on-one-platform-account) to
        `account`. Returns the platform's post ID on success, or
        raises PublishError on failure."""
        raise NotImplementedError

    @abstractmethod
    async def refresh_token_if_needed(self, account: SocialAccount) -> None:
        """Check token expiry and refresh via Secrets Manager if needed."""
        raise NotImplementedError