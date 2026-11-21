import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_onboarding_emails"
down_revision = "0020_workspace_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_deliveries", sa.Column("onboarding_grant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_email_onboarding_grant",
        "email_deliveries",
        "cleaner_access_links",
        ["workspace_id", "onboarding_grant_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_email_kind", "email_deliveries", type_="check")
    op.create_check_constraint(
        "ck_email_kind",
        "email_deliveries",
        "(kind = 'assignment' AND cleaner_schedule_version_id IS NOT NULL AND offer_id IS NULL "
        "AND onboarding_grant_id IS NULL) OR "
        "(kind = 'offer' AND cleaner_schedule_version_id IS NULL AND offer_id IS NOT NULL "
        "AND onboarding_grant_id IS NULL) OR "
        "(kind = 'onboarding' AND cleaner_schedule_version_id IS NULL AND offer_id IS NULL "
        "AND onboarding_grant_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute("SET LOCAL row_security = off")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM email_deliveries WHERE kind = 'onboarding')")):
        raise RuntimeError("Export onboarding email deliveries before downgrading.")
    op.drop_constraint("ck_email_kind", "email_deliveries", type_="check")
    op.create_check_constraint(
        "ck_email_kind",
        "email_deliveries",
        "(kind = 'assignment' AND cleaner_schedule_version_id IS NOT NULL AND offer_id IS NULL) "
        "OR (kind = 'offer' AND cleaner_schedule_version_id IS NULL AND offer_id IS NOT NULL)",
    )
    op.drop_constraint("fk_email_onboarding_grant", "email_deliveries", type_="foreignkey")
    op.drop_column("email_deliveries", "onboarding_grant_id")
