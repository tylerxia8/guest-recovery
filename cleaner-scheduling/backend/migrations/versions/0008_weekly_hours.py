"""Manager-confirmed recurring Eastern working hours."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_weekly_hours"
down_revision = "0007_scheduling_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cleaner_weekly_hours",
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cleaners.id"), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("days", postgresql.JSONB(), nullable=False),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cleaner_weekly_hours TO cleaning_app")


def downgrade() -> None:
    op.drop_table("cleaner_weekly_hours")
