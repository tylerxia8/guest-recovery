"""Link guest workspace manager identities to one primary login."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032_account_workspaces"
down_revision = "0031_action_inbox_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "manager_accounts",
        sa.Column("login_account_id", UUID(as_uuid=True), sa.ForeignKey("manager_accounts.id"), nullable=True),
    )
    op.add_column("manager_accounts", sa.Column("home_access_revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("GRANT UPDATE (home_access_revoked_at) ON manager_accounts TO cleaning_app")
    op.create_index(
        "uq_manager_login_workspace",
        "manager_accounts",
        ["login_account_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM manager_accounts WHERE login_account_id IS NOT NULL)")
    ):
        raise RuntimeError("Review linked accounts before downgrading.")
    op.drop_index("uq_manager_login_workspace", table_name="manager_accounts")
    op.drop_column("manager_accounts", "home_access_revoked_at")
    op.drop_column("manager_accounts", "login_account_id")
