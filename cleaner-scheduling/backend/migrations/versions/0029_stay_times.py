"""Record scheduler-confirmed stay times separately from imported booking times."""

import sqlalchemy as sa
from alembic import op

revision = "0029_stay_times"
down_revision = "0028_guest_touchups"
branch_labels = None
depends_on = None

COLUMNS = ("approved_guest_checkin_at", "approved_guest_checkout_at")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("reservations", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM reservations WHERE approved_guest_checkin_at IS NOT NULL "
            "OR approved_guest_checkout_at IS NOT NULL)"
        )
    ):
        raise RuntimeError("Review confirmed guest stay times before removing their scheduler protection.")
    for name in COLUMNS:
        op.drop_column("reservations", name)
