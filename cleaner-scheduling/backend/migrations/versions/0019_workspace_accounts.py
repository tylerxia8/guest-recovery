import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_workspace_accounts"
down_revision = "0018_workspace_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manager_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signup_key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("signup_request_hash", sa.Text(), nullable=False),
        sa.CheckConstraint("email = lower(email)", name="ck_manager_email_normalized"),
    )
    op.create_table(
        "manager_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manager_accounts.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("login_key_hash", sa.Text(), nullable=True, unique=True),
        sa.Column("login_request_hash", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(login_key_hash IS NULL) = (login_request_hash IS NULL)",
            name="ck_manager_login_idempotency",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_manager_session_expiry"),
    )
    op.create_index("ix_manager_sessions_account_id", "manager_sessions", ["account_id"])
    op.execute("GRANT SELECT, INSERT ON manager_accounts TO cleaning_app")
    op.execute("GRANT SELECT, INSERT ON manager_sessions TO cleaning_app")
    op.execute("GRANT UPDATE (revoked_at) ON manager_sessions TO cleaning_app")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM manager_accounts)")):
        raise RuntimeError("Workspace accounts require a reviewed data transition before downgrade.")
    op.drop_table("manager_sessions")
    op.drop_table("manager_accounts")
