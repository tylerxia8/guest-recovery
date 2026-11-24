"""Allow separately authenticated managers to join an existing workspace."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0030_manager_invitations"
down_revision = "0029_stay_times"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manager_accounts", sa.Column("workspace_owner", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    op.drop_constraint("manager_accounts_workspace_id_key", "manager_accounts", type_="unique")
    op.create_index("ix_manager_accounts_workspace_id", "manager_accounts", ["workspace_id"])
    op.create_index(
        "uq_manager_accounts_workspace_owner",
        "manager_accounts",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("workspace_owner"),
    )
    op.create_table(
        "manager_invitations",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "email"),
    )
    op.execute("ALTER TABLE manager_invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE manager_invitations FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY workspace_isolation ON manager_invitations
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid);
        CREATE POLICY signup_lookup ON manager_invitations FOR SELECT
        USING (nullif(current_setting('app.workspace_id', true), '') IS NULL);
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON manager_invitations TO cleaning_app")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM manager_accounts WHERE NOT workspace_owner)")):
        raise RuntimeError("Review team memberships before restoring the single-account workspace constraint.")
    op.drop_table("manager_invitations")
    op.drop_index("uq_manager_accounts_workspace_owner", table_name="manager_accounts")
    op.drop_index("ix_manager_accounts_workspace_id", table_name="manager_accounts")
    op.create_unique_constraint("manager_accounts_workspace_id_key", "manager_accounts", ["workspace_id"])
    op.drop_column("manager_accounts", "workspace_owner")
