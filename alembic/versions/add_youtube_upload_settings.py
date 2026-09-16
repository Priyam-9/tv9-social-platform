"""add youtube upload settings

Revision ID: 8d41b7c2e9f3
Revises: 5f2c8a91d7e4
Create Date: 2026-09-15 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8d41b7c2e9f3"
down_revision = "5f2c8a91d7e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Post-level YouTube publishing settings.
    op.add_column(
        "posts",
        sa.Column(
            "youtube_privacy_status",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.add_column(
        "posts",
        sa.Column(
            "youtube_category_id",
            sa.String(length=20),
            nullable=True,
        ),
    )

    # Per-target YouTube overrides.
    op.add_column(
        "post_targets",
        sa.Column(
            "youtube_privacy_status_override",
            sa.String(length=20),
            nullable=True,
        ),
    )

    op.add_column(
        "post_targets",
        sa.Column(
            "youtube_category_id_override",
            sa.String(length=20),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "post_targets",
        "youtube_category_id_override",
    )

    op.drop_column(
        "post_targets",
        "youtube_privacy_status_override",
    )

    op.drop_column(
        "posts",
        "youtube_category_id",
    )

    op.drop_column(
        "posts",
        "youtube_privacy_status",
    )