"""add password hash to users

Revision ID: b7e91c4a2d61
Revises: 8d41b7c2e9f3
Create Date: 2026-09-15 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "b7e91c4a2d61"
down_revision = "8d41b7c2e9f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")