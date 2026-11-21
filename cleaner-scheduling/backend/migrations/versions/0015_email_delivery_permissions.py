from alembic import op

revision = "0015_email_delivery_permissions"
down_revision = "0014_automatic_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE ON email_deliveries TO cleaning_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON email_deliveries FROM cleaning_app")
