"""Keep Guest Recovery touch-ups separate from standard departures."""

import sqlalchemy as sa
from alembic import op

revision = "0028_guest_touchups"
down_revision = "0027_submitting_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cleaning_tasks", sa.Column("guest_scope", sa.Text(), nullable=True))
    op.drop_constraint("ck_cleaning_tasks_kind", "cleaning_tasks", type_="check")
    op.create_check_constraint(
        "ck_cleaning_tasks_kind",
        "cleaning_tasks",
        "(kind = 'departure' AND guest_scope IS NULL) OR "
        "(kind = 'guest_touchup' AND guest_scope IS NOT NULL AND deadline_at IS NOT NULL)",
    )
    op.drop_constraint("uq_cleaning_tasks_source_kind", "cleaning_tasks", type_="unique")
    op.create_index(
        "uq_cleaning_tasks_departure",
        "cleaning_tasks",
        ["source_reservation_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'departure'"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaning_tasks WHERE kind = 'guest_touchup')")):
        raise RuntimeError("Existing touch-up tasks require a reviewed data transition before downgrade.")
    op.drop_index("uq_cleaning_tasks_departure", table_name="cleaning_tasks")
    op.create_unique_constraint("uq_cleaning_tasks_source_kind", "cleaning_tasks", ["source_reservation_id", "kind"])
    op.drop_constraint("ck_cleaning_tasks_kind", "cleaning_tasks", type_="check")
    op.create_check_constraint("ck_cleaning_tasks_kind", "cleaning_tasks", "kind = 'departure'")
    op.drop_column("cleaning_tasks", "guest_scope")
