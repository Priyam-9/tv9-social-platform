import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SocialAccountCreate(BaseModel):
    platform: str
    account_name: str
    secrets_manager_arn: str


class SocialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: str
    account_name: str
    is_active: bool
    created_at: datetime


class PostCreate(BaseModel):
    title: str | None = None
    caption: str | None = None
    media_s3_key: str | None = None
    media_type: str | None = None
    created_by: str | None = None
    # List of account IDs to publish to, with when to publish each
    target_account_ids: list[uuid.UUID]
    scheduled_for: datetime


class PostTargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    social_account_id: uuid.UUID
    scheduled_for: datetime
    status: str
    platform_post_id: str | None
    error_message: str | None
    attempts: int
    platform: str | None = None
    account_name: str | None = None


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    caption: str | None
    media_type: str | None
    created_by: str | None
    created_at: datetime
    targets: list[PostTargetOut] = []
