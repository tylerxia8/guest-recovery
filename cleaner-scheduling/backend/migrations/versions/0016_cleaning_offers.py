import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_cleaning_offers"
down_revision = "0015_email_delivery_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cleaning_offers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cleaning_tasks.id"), nullable=False),
        sa.Column("original_cleaner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cleaners.id"), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_cleaner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cleaners.id")),
        sa.Column("claimed_assignment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assignments.id")),
        sa.CheckConstraint("status IN ('open','expired','claimed','canceled')", name="ck_cleaning_offer_status"),
        sa.CheckConstraint("expires_at > opened_at", name="ck_cleaning_offer_response_window"),
    )
    op.create_index(
        "uq_cleaning_offers_active_task",
        "cleaning_offers",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open','expired')"),
    )
    op.create_index("ix_cleaning_offers_response_due", "cleaning_offers", ["status", "expires_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE ON cleaning_offers TO cleaning_app")
    op.drop_constraint("ck_ack_method", "acknowledgments", type_="check")
    op.create_check_constraint("ck_ack_method", "acknowledgments", "method IN ('sms','manager_recorded','portal')")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM cleaning_offers)")):
        raise RuntimeError("Cleaning offer history requires a reviewed data transition before downgrade.")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM acknowledgments WHERE method = 'portal')")):
        raise RuntimeError("Portal acceptance history requires a reviewed data transition before downgrade.")
    op.drop_constraint("ck_ack_method", "acknowledgments", type_="check")
    op.create_check_constraint("ck_ack_method", "acknowledgments", "method IN ('sms','manager_recorded')")
    op.drop_table("cleaning_offers")
