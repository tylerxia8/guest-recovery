import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_offer_emails"
down_revision = "0016_cleaning_offers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_deliveries", sa.Column("kind", sa.Text(), nullable=False, server_default="assignment"))
    op.add_column("email_deliveries", sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_email_offer", "email_deliveries", "cleaning_offers", ["offer_id"], ["id"])
    op.alter_column("email_deliveries", "cleaner_schedule_version_id", nullable=True)
    op.create_check_constraint(
        "ck_email_kind",
        "email_deliveries",
        "(kind = 'assignment' AND cleaner_schedule_version_id IS NOT NULL AND offer_id IS NULL) "
        "OR (kind = 'offer' AND cleaner_schedule_version_id IS NULL AND offer_id IS NOT NULL)",
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM email_deliveries WHERE kind = 'offer')")):
        raise RuntimeError("Offer email history requires a reviewed transition before downgrade.")
    op.drop_constraint("ck_email_kind", "email_deliveries", type_="check")
    op.drop_constraint("fk_email_offer", "email_deliveries", type_="foreignkey")
    op.drop_column("email_deliveries", "offer_id")
    op.drop_column("email_deliveries", "kind")
    op.alter_column("email_deliveries", "cleaner_schedule_version_id", nullable=False)
