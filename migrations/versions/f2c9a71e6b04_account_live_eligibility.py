"""Operator-only account live eligibility, off for every existing/new connection."""

import sqlalchemy as sa
from alembic import op

revision = "f2c9a71e6b04"
down_revision = "e6b41c9a203d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campushallen_accounts",
        sa.Column("live_booking_enabled", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "campushallen_accounts",
        sa.Column("live_booking_claim_limit", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("campushallen_accounts", sa.Column("live_booking_activity_id", sa.Integer()))


def downgrade():
    op.drop_column("campushallen_accounts", "live_booking_activity_id")
    op.drop_column("campushallen_accounts", "live_booking_claim_limit")
    op.drop_column("campushallen_accounts", "live_booking_enabled")
