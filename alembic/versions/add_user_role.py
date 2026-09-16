"""add explicit user roles

Revision ID: add_user_role
Revises: b7e91c4a2d61
Create Date: 2026-09-16

Adds the authoritative role column to users while preserving the existing
is_admin column for backwards compatibility during the role rollout.

Existing is_admin=True users are promoted to role="admin"; all other
existing users become role="content_manager".
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4f2a7d91e63"
down_revision = "b7e91c4a2d61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=50),
            nullable=False,
            server_default="content_manager",
        ),
    )

    # Preserve the meaning of the existing admin flag for current users.
    op.execute(
        sa.text(
            """
            UPDATE users
            SET role = 'admin'
            WHERE is_admin = TRUE
            """
        )
    )


def downgrade() -> None:
    op.drop_column("users", "role")
