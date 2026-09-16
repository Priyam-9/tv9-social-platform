"""add post metadata and target overrides

Revision ID: 5f2c8a91d7e4
Revises: 0774cb2f8523
Create Date: 2026-09-14
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5f2c8a91d7e4"
down_revision = "0774cb2f8523"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------
    # social_accounts
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE social_accounts
        ADD COLUMN IF NOT EXISTS connected_at TIMESTAMPTZ
        """
    )

    op.execute(
        """
        ALTER TABLE social_accounts
        ADD COLUMN IF NOT EXISTS default_language VARCHAR(10)
        """
    )

    # ---------------------------------------------------------
    # posts
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE posts
        ADD COLUMN IF NOT EXISTS thumbnail_s3_key VARCHAR(500)
        """
    )

    op.execute(
        """
        ALTER TABLE posts
        ADD COLUMN IF NOT EXISTS youtube_tags JSON
        """
    )

    # ---------------------------------------------------------
    # post_targets
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS language VARCHAR(10)
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS title_override VARCHAR(500)
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS caption_override TEXT
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS media_s3_key_override VARCHAR(500)
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS thumbnail_s3_key_override VARCHAR(500)
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        ADD COLUMN IF NOT EXISTS youtube_tags_override JSON
        """
    )


def downgrade() -> None:
    # ---------------------------------------------------------
    # post_targets
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS youtube_tags_override
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS thumbnail_s3_key_override
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS media_s3_key_override
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS caption_override
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS title_override
        """
    )

    op.execute(
        """
        ALTER TABLE post_targets
        DROP COLUMN IF EXISTS language
        """
    )

    # ---------------------------------------------------------
    # posts
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE posts
        DROP COLUMN IF EXISTS youtube_tags
        """
    )

    op.execute(
        """
        ALTER TABLE posts
        DROP COLUMN IF EXISTS thumbnail_s3_key
        """
    )

    # ---------------------------------------------------------
    # social_accounts
    # ---------------------------------------------------------
    op.execute(
        """
        ALTER TABLE social_accounts
        DROP COLUMN IF EXISTS default_language
        """
    )

    op.execute(
        """
        ALTER TABLE social_accounts
        DROP COLUMN IF EXISTS connected_at
        """
    )