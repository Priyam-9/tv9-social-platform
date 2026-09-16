import hashlib
import secrets as secrets_module
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Integer, JSON, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """
    A person who uses the dashboard.

    Access is scoped by which social_accounts they are explicitly granted
    through UserAccountAccess.

    The `role` field is the authoritative application role:
      - admin: full account visibility and administrative permissions
      - content_manager: access only to explicitly granted accounts

    `is_admin` is retained as a legacy compatibility field during the role
    migration. New authorization code must use `role`.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Authoritative application role.
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="content_manager",
        server_default="content_manager",
    )

    # Legacy compatibility field. Do not use this for new authorization logic.
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )

    # Plaintext passwords are never stored.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    account_access: Mapped[list["UserAccountAccess"]] = relationship(
        back_populates="user"
    )


class UserAccountAccess(Base):
    """
    Grants one user access to one social account.

    Content managers receive explicit grants through this table. An admin
    receives unrestricted account access through the `admin` role and does
    not need individual grant rows.
    """

    __tablename__ = "user_account_access"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("social_accounts.id"),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="account_access")
    social_account: Mapped["SocialAccount"] = relationship()


class Session(Base):
    """
    A logged-in browser session.

    POST /auth/login verifies the user's email and password, creates a
    random session token, and sends the raw token to the browser as an
    HttpOnly cookie. The database stores only a SHA-256 hash of that token.

    Authenticated requests resolve the session from the cookie, verify the
    stored token hash and expiry, and then load the associated User row.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    user: Mapped["User"] = relationship()


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_session_token() -> str:
    # 32 bytes of randomness, URL-safe.
    return secrets_module.token_urlsafe(32)


class SocialAccount(Base):
    """
    One row per connected platform account.

    OAuth credentials are never stored in this table. The
    `secrets_manager_arn` field is an identifier/pointer used by the
    secrets storage abstraction. In local development it maps to the
    local secrets store; in deployed environments it maps to AWS Secrets
    Manager.
    """

    __tablename__ = "social_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # youtube, instagram, facebook, x
    account_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    secrets_manager_arn: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Timestamp of the most recent completed OAuth consent flow.
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Channel's home/default language, e.g. "hi" or "mr".
    # A post target may still override this value.
    default_language: Mapped[str | None] = mapped_column(String(10))

    targets: Mapped[list["PostTarget"]] = relationship(
        back_populates="social_account"
    )


class Post(Base):
    """
    A single piece of content created once, then fanned out into one or
    more PostTarget rows.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    title: Mapped[str | None] = mapped_column(String(500))
    caption: Mapped[str | None] = mapped_column(Text)
    media_s3_key: Mapped[str | None] = mapped_column(String(500))
    media_type: Mapped[str | None] = mapped_column(
        String(20)
    )  # video, image, text

    # Optional custom thumbnail shared by all targets unless a target
    # provides its own thumbnail override.
    thumbnail_s3_key: Mapped[str | None] = mapped_column(String(500))

    # YouTube-native tags.
    youtube_tags: Mapped[list[str] | None] = mapped_column(JSON)

    # Default YouTube upload settings for this post. Individual targets
    # can override these when a channel needs different settings.
    youtube_privacy_status: Mapped[str | None] = mapped_column(String(20))
    youtube_category_id: Mapped[str | None] = mapped_column(String(20))

    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    targets: Mapped[list["PostTarget"]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
    )


class PostTarget(Base):
    """
    Tracks the publish status of one post on one platform account.

    Each target is independently publishable/retryable, so success or
    failure on one platform does not overwrite the state of another.
    """

    __tablename__ = "post_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id"),
        nullable=False,
    )
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("social_accounts.id"),
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
    )
    # pending, publishing, published, failed

    platform_post_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Target language. The post-creation layer resolves account default
    # language when this value is not explicitly supplied.
    language: Mapped[str | None] = mapped_column(String(10))

    title_override: Mapped[str | None] = mapped_column(String(500))
    caption_override: Mapped[str | None] = mapped_column(Text)

    # Optional target-specific media/thumbnail overrides.
    media_s3_key_override: Mapped[str | None] = mapped_column(String(500))
    thumbnail_s3_key_override: Mapped[str | None] = mapped_column(String(500))

    # Optional target-specific YouTube tags/settings.
    youtube_tags_override: Mapped[list[str] | None] = mapped_column(JSON)
    youtube_privacy_status_override: Mapped[str | None] = mapped_column(
        String(20)
    )
    youtube_category_id_override: Mapped[str | None] = mapped_column(
        String(20)
    )

    post: Mapped["Post"] = relationship(back_populates="targets")
    social_account: Mapped["SocialAccount"] = relationship(
        back_populates="targets"
    )

    @property
    def platform(self) -> str | None:
        return self.social_account.platform if self.social_account else None

    @property
    def account_name(self) -> str | None:
        return self.social_account.account_name if self.social_account else None

    @property
    def effective_title(self) -> str | None:
        return self.title_override or (
            self.post.title if self.post else None
        )

    @property
    def effective_caption(self) -> str | None:
        return self.caption_override or (
            self.post.caption if self.post else None
        )

    @property
    def effective_media_s3_key(self) -> str | None:
        return self.media_s3_key_override or (
            self.post.media_s3_key if self.post else None
        )

    @property
    def effective_thumbnail_s3_key(self) -> str | None:
        return self.thumbnail_s3_key_override or (
            self.post.thumbnail_s3_key if self.post else None
        )

    @property
    def effective_youtube_tags(self) -> list[str]:
        if self.youtube_tags_override is not None:
            return self.youtube_tags_override

        if self.post and self.post.youtube_tags:
            return self.post.youtube_tags

        return []

    @property
    def effective_youtube_privacy_status(self) -> str | None:
        return self.youtube_privacy_status_override or (
            self.post.youtube_privacy_status if self.post else None
        )

    @property
    def effective_youtube_category_id(self) -> str | None:
        return self.youtube_category_id_override or (
            self.post.youtube_category_id if self.post else None
        )
