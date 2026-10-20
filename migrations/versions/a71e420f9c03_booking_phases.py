"""Safe mutation phase evidence and explicit ongoing live approval.

Additive migration preserves all historical attempts and durable claims.
"""

import sqlalchemy as sa
from alembic import op

revision = "a71e420f9c03"
down_revision = "f2c9a71e6b04"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campushallen_accounts",
        sa.Column("live_booking_ongoing", sa.Boolean(), nullable=False, server_default="0"),
    )
    for name, kind in (
        ("phase", sa.String(40)),
        ("http_status", sa.Integer()),
        ("endpoint_category", sa.String(30)),
        ("verification_outcome", sa.String(20)),
    ):
        op.add_column("booking_attempts", sa.Column(name, kind))


def downgrade():
    for name in ("verification_outcome", "endpoint_category", "http_status", "phase"):
        op.drop_column("booking_attempts", name)
    op.drop_column("campushallen_accounts", "live_booking_ongoing")
