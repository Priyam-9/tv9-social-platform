import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Text, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    """
    A person who uses the dashboard. Access is scoped by which
    social_accounts they're explicitly granted (see UserAccountAccess) —
    an Admin isn't a fundamentally different code path, just a user
    with is_admin=True, which grants visibility into every account
    without needing individual grants for each one (important
    practically: when a new YouTube channel is connected later, admins
    should see it immediately, not need re-granting one by one).
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account_access: Mapped[list["UserAccountAccess"]] = relationship(back_populates="user")


class UserAccountAccess(Base):
    """
    Grants one user access to one social_account. A Content Manager
    responsible for a single YouTube channel has exactly one row here;
    someone managing several channels has one row per channel. Admins
    don't need rows here at all — is_admin=True on the User already
    grants everything.
    """

    __tablename__ = "user_account_access"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    social_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("social_accounts.id"), nullable=False)

    user: Mapped["User"] = relationship(back_populates="account_access")
    social_account: Mapped["SocialAccount"] = relationship()


class SocialAccount(Base):
    """
    One row per connected platform account (e.g. TV9's YouTube channel,
    TV9's Instagram Business account, etc). We never store raw OAuth
    tokens here — only a pointer to where they live in AWS Secrets
    Manager. Locally, this field can point to a local secrets stub
    until Phase 4 wires up real Secrets Manager access.
    """

    __tablename__ = "social_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # youtube, instagram, facebook, x
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    secrets_manager_arn: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When OAuth consent was last completed for this account. In Google's
    # "Testing" publishing status, the refresh token itself expires 7 days
    # after consent was granted — refreshing the access token does NOT
    # reset this clock. This field is what lets the dashboard warn you
    # before that happens, instead of you finding out from a failed
    # publish (see the 401 incident this was built to prevent).
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # This channel's "home" language, e.g. TV9 Hindi channel -> "hi",
    # TV9 Marathi channel -> "mr". Lets the dashboard pre-fill a
    # target's language the moment you select that channel, instead of
    # you re-picking it from scratch on every single post - which is
    # exactly the kind of manual step that gets skipped by mistake
    # under deadline pressure. Still overridable per-post for the rare
    # exception (e.g. an English-language clip going on the Hindi
    # channel).
    default_language: Mapped[str | None] = mapped_column(String(10))
    # The language this channel is dedicated to (e.g. TV9 Hindi -> "hi",
    # TV9 Marathi -> "mr"). Purely a UI convenience: the create-form
    # auto-selects this as the target's language the moment the account
    # is checked, instead of requiring it to be picked by hand every
    # time - which is exactly the kind of manual step that gets missed
    # under time pressure and sends the wrong language to the wrong
    # channel. Still fully overridable per post.
    default_language: Mapped[str | None] = mapped_column(String(10))

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="social_account")


class Post(Base):
    """
    A single piece of content created once, then fanned out to one or
    more platforms via PostTarget rows.
    """

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    title: Mapped[str | None] = mapped_column(String(500))
    caption: Mapped[str | None] = mapped_column(Text)
    media_s3_key: Mapped[str | None] = mapped_column(String(500))
    media_type: Mapped[str | None] = mapped_column(String(20))  # video, image, text
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # cascade="all, delete-orphan": deleting a Post deletes its
    # PostTarget rows in the same transaction. Without this, SQLAlchemy
    # only issues DELETE on the posts row, and Postgres rejects it with
    # a foreign key violation the moment a target exists — that
    # unhandled FK violation is what was surfacing as the 500 on
    # DELETE /posts/{id}.
    targets: Mapped[list["PostTarget"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class PostTarget(Base):
    """
    Tracks the publish status of ONE post on ONE platform account.
    This is what lets YouTube succeed while X fails, independently,
    and lets you retry just the failed one.
    """

    __tablename__ = "post_targets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id"), nullable=False)
    social_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("social_accounts.id"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending, publishing, published, failed
    platform_post_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-target language + optional native-script overrides. If a
    # target has no override, publishing falls back to the Post's
    # default title/caption (see effective_title/effective_caption
    # below) — so you're not forced to fill in every language on
    # every post.
    language: Mapped[str | None] = mapped_column(String(10))  # e.g. "hi", "te", "bn", "en"
    title_override: Mapped[str | None] = mapped_column(String(500))
    caption_override: Mapped[str | None] = mapped_column(Text)
    # Per-target media file override, e.g. a separately dubbed Marathi
    # cut of the same segment. Falls back to the Post's default
    # media_s3_key when unset - most of the time every channel shares
    # one file, this only matters on the days it doesn't.
    media_s3_key_override: Mapped[str | None] = mapped_column(String(500))

    post: Mapped["Post"] = relationship(back_populates="targets")
    social_account: Mapped["SocialAccount"] = relationship(back_populates="targets")

    @property
    def platform(self) -> str | None:
        return self.social_account.platform if self.social_account else None

    @property
    def account_name(self) -> str | None:
        return self.social_account.account_name if self.social_account else None

    @property
    def effective_title(self) -> str | None:
        return self.title_override or (self.post.title if self.post else None)

    @property
    def effective_caption(self) -> str | None:
        return self.caption_override or (self.post.caption if self.post else None)

    @property
    def effective_media_s3_key(self) -> str | None:
        return self.media_s3_key_override or (self.post.media_s3_key if self.post else None)