import sqlalchemy as sa
from alembic import op

revision = "0026_confirmed_checkout"
down_revision = "0025_account_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaning_tasks", sa.Column("confirmed_checkout_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cleaning_tasks", sa.Column("confirmed_checkout_reservation_version", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_task_confirmed_checkout",
        "cleaning_tasks",
        "(confirmed_checkout_at IS NULL) = (confirmed_checkout_reservation_version IS NULL) "
        "AND (confirmed_checkout_reservation_version IS NULL OR confirmed_checkout_reservation_version > 0)",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE cleaning_tasks IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM cleaning_tasks WHERE confirmed_checkout_at IS NOT NULL)")
    ):
        raise RuntimeError("Confirmed departures require a reviewed transition before downgrade.")
    op.drop_constraint("ck_task_confirmed_checkout", "cleaning_tasks", type_="check")
    op.drop_column("cleaning_tasks", "confirmed_checkout_reservation_version")
    op.drop_column("cleaning_tasks", "confirmed_checkout_at")
