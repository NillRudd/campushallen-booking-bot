"""One Campushallen connection per user; stable recurring-rule duplicate protection."""

import sqlalchemy as sa
from alembic import op

revision = "e6b41c9a203d"
down_revision = "d8426c9ef017"
branch_labels = None
depends_on = None


def upgrade():
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT user_id FROM campushallen_accounts GROUP BY user_id HAVING count(*) > 1"
            )
        )
        .scalars()
        .all()
    )
    if duplicates:
        raise RuntimeError(
            "Cannot enforce one Campushallen account per user: duplicate connections for user IDs "
            + ", ".join(map(str, duplicates))
            + ". Resolve these explicitly before retrying. No accounts have been deleted."
        )
    # Unique indexes avoid rebuilding a parent table and cascading SQLite child deletion.
    op.create_index(
        "uq_campushallen_account_user", "campushallen_accounts", ["user_id"], unique=True
    )
    op.add_column(
        "booking_attempts",
        sa.Column("is_preview", sa.Boolean(), nullable=True),
    )
    op.add_column("booking_rules", sa.Column("selection_key", sa.String(64), nullable=True))
    op.create_index(
        "uq_rule_selection",
        "booking_rules",
        ["campushallen_account_id", "selection_key"],
        unique=True,
    )


def downgrade():
    op.drop_column("booking_attempts", "is_preview")
    op.drop_index("uq_rule_selection", table_name="booking_rules")
    op.drop_column("booking_rules", "selection_key")
    op.drop_index("uq_campushallen_account_user", table_name="campushallen_accounts")
