from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import Post, SocialAccount


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

    Next phase: we'll implement YouTubeAdapter first (simplest OAuth
    flow), then InstagramAdapter, FacebookAdapter, and XAdapter.
    """

    @abstractmethod
    async def publish(self, post: Post, account: SocialAccount) -> PublishResult:
        """Publish `post` to `account`. Returns the platform's post ID
        on success, or raises PublishError on failure."""
        raise NotImplementedError

    @abstractmethod
    async def refresh_token_if_needed(self, account: SocialAccount) -> None:
        """Check token expiry and refresh via Secrets Manager if needed."""
        raise NotImplementedError
