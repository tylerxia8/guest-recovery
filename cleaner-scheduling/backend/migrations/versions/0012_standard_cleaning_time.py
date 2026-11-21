import sqlalchemy as sa
from alembic import op

revision = "0012_standard_cleaning_time"
down_revision = "0011_cleaner_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_cleaning_tasks_duration_source", "cleaning_tasks", type_="check")
    op.create_check_constraint(
        "ck_cleaning_tasks_duration_source",
        "cleaning_tasks",
        "duration_source IN ('property_override','bedroom_default','standard_default','missing')",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM cleaning_tasks WHERE duration_source = 'standard_default')")
    ):
        raise RuntimeError("Standard cleaning durations require a reviewed data transition before downgrade.")
    op.drop_constraint("ck_cleaning_tasks_duration_source", "cleaning_tasks", type_="check")
    op.create_check_constraint(
        "ck_cleaning_tasks_duration_source",
        "cleaning_tasks",
        "duration_source IN ('property_override','bedroom_default','missing')",
    )
