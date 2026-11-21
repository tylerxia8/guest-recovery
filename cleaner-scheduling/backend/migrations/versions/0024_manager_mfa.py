import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_manager_mfa"
down_revision = "0023_cleaner_preferred_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manager_accounts", sa.Column("mfa_secret", sa.LargeBinary(), nullable=True))
    op.add_column("manager_accounts", sa.Column("mfa_pending_secret", sa.LargeBinary(), nullable=True))
    op.add_column("manager_accounts", sa.Column("mfa_pending_session_hash", sa.Text(), nullable=True))
    op.add_column("manager_accounts", sa.Column("mfa_pending_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("manager_accounts", sa.Column("mfa_last_counter", sa.BigInteger(), nullable=True))
    op.add_column(
        "manager_accounts",
        sa.Column("mfa_recovery_hashes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_check_constraint(
        "ck_manager_mfa_pending",
        "manager_accounts",
        "(mfa_pending_secret IS NULL) = (mfa_pending_expires_at IS NULL) "
        "AND (mfa_pending_secret IS NULL) = (mfa_pending_session_hash IS NULL)",
    )
    op.create_check_constraint(
        "ck_manager_mfa_state",
        "manager_accounts",
        "(mfa_secret IS NULL AND mfa_last_counter IS NULL AND mfa_recovery_hashes = '[]'::jsonb) "
        "OR (mfa_secret IS NOT NULL AND mfa_last_counter IS NOT NULL AND mfa_pending_secret IS NULL)",
    )
    op.execute(
        "GRANT UPDATE (mfa_secret, mfa_pending_secret, mfa_pending_session_hash, mfa_pending_expires_at, "
        "mfa_last_counter, mfa_recovery_hashes) ON manager_accounts TO cleaning_app"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE manager_accounts IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM manager_accounts WHERE mfa_secret IS NOT NULL)")):
        raise RuntimeError("Disable manager MFA through authenticated account settings before downgrading.")
    op.drop_constraint("ck_manager_mfa_state", "manager_accounts", type_="check")
    op.drop_constraint("ck_manager_mfa_pending", "manager_accounts", type_="check")
    op.drop_column("manager_accounts", "mfa_recovery_hashes")
    op.drop_column("manager_accounts", "mfa_last_counter")
    op.drop_column("manager_accounts", "mfa_pending_expires_at")
    op.drop_column("manager_accounts", "mfa_pending_session_hash")
    op.drop_column("manager_accounts", "mfa_pending_secret")
    op.drop_column("manager_accounts", "mfa_secret")
