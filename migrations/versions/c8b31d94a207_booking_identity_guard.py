"""Exclusive live identity ownership for the controlled multi-user beta.

Revision ID: c8b31d94a207
Revises: b2d934a8e710
"""

import sqlalchemy as sa
from alembic import op

revision = "c8b31d94a207"
down_revision = "b2d934a8e710"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_identities",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("campushallen_accounts.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("external_label", sa.String(64), nullable=True),
        sa.Column("external_active", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )


def downgrade():
    # An older worker does not enforce this safety registry. Never erase it
    # while a live connection or legacy exclusion exists.
    db = op.get_bind()
    if db.execute(sa.text("SELECT COUNT(*) FROM booking_identities")).scalar():
        raise RuntimeError(
            "Preserve live identity ownership; disable sources and review rollback first"
        )
    op.drop_table("booking_identities")
