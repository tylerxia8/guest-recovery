"""Preserve original request IDs for migrated cleaner returns."""

import sqlalchemy as sa
from alembic import op

revision = "0034_legacy_return_lookup"
down_revision = "0033_manager_session_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cleaning_tasks")}
    if "external_return_request_id" not in columns:
        op.add_column("cleaning_tasks", sa.Column("external_return_request_id", sa.UUID(), nullable=True))
        return
    op.execute("UPDATE cleaning_tasks SET kind='guest_touchup', guest_scope=return_scope WHERE kind='return'")


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM cleaning_tasks WHERE external_return_request_id IS NOT NULL)")
    ):
        raise RuntimeError("Review migrated cleaner return references before downgrading.")
    op.drop_column("cleaning_tasks", "external_return_request_id")
