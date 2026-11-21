"""Allow operational assignments without compensation; preserve historical finance.

Revision ID: 0007_scheduling_only
Revises: 0006_payments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_scheduling_only"
down_revision: str | None = "0006_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("assignments", "rate_id", nullable=True)
    op.alter_column("assignments", "amount_cents", nullable=True)


def downgrade() -> None:
    # Do not invent compensation or delete scheduling history to satisfy M7.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM assignments WHERE rate_id IS NULL OR amount_cents IS NULL)")
    ):
        raise RuntimeError("Scheduling-only assignments require a reviewed data transition before downgrade.")
    op.alter_column("assignments", "rate_id", nullable=False)
    op.alter_column("assignments", "amount_cents", nullable=False)
