import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_workspace_connections"
down_revision = "0019_workspace_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_connections",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"),
    )
    op.execute("ALTER TABLE workspace_connections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_connections FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY workspace_isolation ON workspace_connections "
        "USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
        "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON workspace_connections TO cleaning_app")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM workspace_connections)")):
        raise RuntimeError("Export workspace connections before downgrading.")
    op.drop_table("workspace_connections")
