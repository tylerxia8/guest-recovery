"""Add submitting_at to email_deliveries for pre-send vs post-send distinction.

This timestamp marks when we started submitting to Gmail, allowing stranded-row
recovery to distinguish between:
- Rows that crashed BEFORE submission (submitting_at IS NULL) → requeue
- Rows that crashed DURING/AFTER submission (submitting_at IS NOT NULL) → uncertain
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_submitting_at"
down_revision = "0026_confirmed_checkout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_deliveries", sa.Column("submitting_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE email_deliveries
        SET submitting_at = claimed_at
        WHERE status = 'sending'
    """)


def downgrade() -> None:
    op.drop_column("email_deliveries", "submitting_at")
