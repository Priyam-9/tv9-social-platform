import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


ROLE_ADMIN = "admin"
ROLE_CONTENT_MANAGER = "content_manager"
UserRole = Literal[ROLE_ADMIN, ROLE_CONTENT_MANAGER]


class SocialAccountCreate(BaseModel):
    platform: str
    account_name: str
    secrets_manager_arn: str


class UserCreate(BaseModel):
    email: str
    display_name: str

    # Retained only for compatibility with older callers. The users router
    # ignores this value and always creates new users as content_manager.
    is_admin: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: UserRole
    is_admin: bool
    created_at: datetime


class SocialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: str
    account_name: str
    is_active: bool
    created_at: datetime
    connected_at: datetime | None = None
    default_language: str | None = None


class PostTargetCreate(BaseModel):
    account_id: uuid.UUID

    language: str | None = None

    title_override: str | None = None

    caption_override: str | None = None

    # None means use the post-level YouTube tags.
    youtube_tags: list[str] | None = None

    media_s3_key_override: str | None = None

    thumbnail_s3_key_override: str | None = None

    # Optional target-specific YouTube publishing settings.
    youtube_privacy_status_override: str | None = None
    youtube_category_id_override: str | None = None


class PostCreate(BaseModel):
    title: str | None = None

    caption: str | None = None

    media_s3_key: str | None = None

    media_type: str | None = None

    thumbnail_s3_key: str | None = None

    youtube_tags: list[str] = Field(
        default_factory=list
    )

    youtube_privacy_status: str | None = None
    youtube_category_id: str | None = None

    created_by: str | None = None

    targets: list[PostTargetCreate]

    scheduled_for: datetime


class PostTargetOut(BaseModel):
    """
    API response for one PostTarget.

    Effective values are resolved by the SQLAlchemy model using:

        target override -> post default -> empty/None
    """

    model_config = ConfigDict(
        from_attributes=True
    )

    id: uuid.UUID

    social_account_id: uuid.UUID

    scheduled_for: datetime

    status: str

    platform_post_id: str | None = None

    error_message: str | None = None

    attempts: int

    platform: str | None = None

    account_name: str | None = None

    language: str | None = None

    title_override: str | None = None

    caption_override: str | None = None

    media_s3_key_override: str | None = None

    thumbnail_s3_key_override: str | None = None

    youtube_tags: list[str] = Field(
        default_factory=list,
        validation_alias="effective_youtube_tags",
    )

    youtube_privacy_status: str | None = Field(
        default=None,
        validation_alias="effective_youtube_privacy_status",
    )

    youtube_category_id: str | None = Field(
        default=None,
        validation_alias="effective_youtube_category_id",
    )

    @field_validator(
        "youtube_tags",
        mode="before",
    )
    @classmethod
    def normalize_youtube_tags(cls, value):
        if value is None:
            return []

        if isinstance(value, list):
            return value

        return []


class PostOut(BaseModel):
    model_config = ConfigDict(
        from_attributes=True
    )

    id: uuid.UUID

    title: str | None = None

    caption: str | None = None

    media_type: str | None = None

    thumbnail_s3_key: str | None = None

    youtube_tags: list[str] = Field(
        default_factory=list
    )

    youtube_privacy_status: str | None = None

    youtube_category_id: str | None = None

    created_by: str | None = None

    created_at: datetime

    targets: list[PostTargetOut] = Field(
        default_factory=list
    )

    @field_validator(
        "youtube_tags",
        mode="before",
    )
    @classmethod
    def normalize_youtube_tags(cls, value):
        if value is None:
            return []

        if isinstance(value, list):
            return value

        return []
