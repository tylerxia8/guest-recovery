"""Optional individual caps and private starting-address routing cache."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_cleaner_inputs"
down_revision = "0008_weekly_hours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("max_jobs", "max_service_minutes", "max_duty_minutes"):
        op.alter_column("cleaners", column, nullable=True)
    op.add_column("cleaner_weekly_hours", sa.Column("starting_address_ciphertext", postgresql.BYTEA(), nullable=True))
    op.add_column("cleaner_weekly_hours", sa.Column("origin_routes", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM cleaners WHERE max_jobs IS NULL "
            "OR max_service_minutes IS NULL OR max_duty_minutes IS NULL)"
        )
    ):
        raise RuntimeError("Hours-based cleaners require a reviewed transition before restoring mandatory caps.")
    op.drop_column("cleaner_weekly_hours", "origin_routes")
    op.drop_column("cleaner_weekly_hours", "starting_address_ciphertext")
    for column in ("max_jobs", "max_service_minutes", "max_duty_minutes"):
        op.alter_column("cleaners", column, nullable=False)
