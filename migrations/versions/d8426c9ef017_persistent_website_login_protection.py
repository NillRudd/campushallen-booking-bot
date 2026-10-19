"""Persistent website-login limits, separate from Campushallen authentication.

Revision ID: d8426c9ef017
Revises: 7a36bb9c35dc
"""

import sqlalchemy as sa
from alembic import op

revision = "d8426c9ef017"
down_revision = "7a36bb9c35dc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "website_login_buckets",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="nonnegative_login_attempts"),
    )
    op.create_index("ix_website_login_buckets_updated_at", "website_login_buckets", ["updated_at"])


def downgrade():
    op.drop_table("website_login_buckets")
