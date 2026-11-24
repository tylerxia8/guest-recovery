"""Store workspace-scoped ownership for action inbox items."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0031_action_inbox_claims"
down_revision = "0030_manager_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_inbox_claims",
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("item_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("assignee_subject", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN ('cleaner', 'guest-replies', 'supplies', 'maintenance')"),
    )
    op.execute("""
        ALTER TABLE action_inbox_claims ENABLE ROW LEVEL SECURITY;
        ALTER TABLE action_inbox_claims FORCE ROW LEVEL SECURITY;
        CREATE POLICY workspace_isolation ON action_inbox_claims
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
        GRANT SELECT, INSERT, UPDATE ON action_inbox_claims TO cleaning_app;
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM action_inbox_claims)")):
        raise RuntimeError("Review action inbox claim history before removing the table.")
    op.drop_table("action_inbox_claims")
