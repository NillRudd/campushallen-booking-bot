"""Explicit single-use manual reconciliation; preserve the original claim forever."""

import sqlalchemy as sa
from alembic import op

revision = "b2d934a8e710"
down_revision = "a71e420f9c03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "campushallen_account_id",
            sa.Integer(),
            sa.ForeignKey("campushallen_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column(
            "approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("original_claim_created_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime()),
        sa.UniqueConstraint(
            "campushallen_account_id", "activity_id", name="uq_reconciliation_account_activity"
        ),
    )
    op.create_index(
        "ix_reconciliation_account_consumed",
        "booking_reconciliations",
        ["campushallen_account_id", "consumed_at"],
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM booking_reconciliations WHERE consumed_at IS NOT NULL LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError(
            "Cannot downgrade consumed manual approvals: preserve mutation allowance and use a schema-compatible image"
        )
    op.drop_index("ix_reconciliation_account_consumed", table_name="booking_reconciliations")
    op.drop_table("booking_reconciliations")
