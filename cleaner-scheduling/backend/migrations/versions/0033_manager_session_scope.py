"""Protect manager access when importing legacy delegated stay credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0033_manager_session_scope"
down_revision = "0032_account_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    scope = next((column for column in inspector.get_columns("manager_sessions") if column["name"] == "scope"), None)
    if scope is None:
        op.add_column("manager_sessions", sa.Column("scope", sa.Text(), nullable=False, server_default="manager"))
        op.create_check_constraint("ck_manager_session_scope", "manager_sessions", "scope IN ('manager', 'stay')")
        return
    constraints = {item["name"] for item in inspector.get_check_constraints("manager_sessions")}
    if scope["nullable"] or scope["default"] != "'manager'::text" or "ck_manager_session_scope" not in constraints:
        raise RuntimeError("Existing manager session scopes require review.")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM manager_sessions WHERE scope <> 'manager')")):
        raise RuntimeError("Review delegated credentials before dropping session scope.")
    op.drop_constraint("ck_manager_session_scope", "manager_sessions", type_="check")
    op.drop_column("manager_sessions", "scope")
