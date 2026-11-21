import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_automatic_email"
down_revision = "0013_cleaner_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cleaners.id"), nullable=False),
        sa.Column(
            "cleaner_schedule_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cleaner_schedule_versions.id"),
            nullable=False,
        ),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("message_key", sa.Text(), nullable=False, unique=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("snapshot_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.Text()),
        sa.CheckConstraint(
            "status IN ('queued','sending','accepted','uncertain','failed','stale')", name="ck_email_delivery_status"
        ),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="ck_email_delivery_attempts"),
    )
    op.create_index("ix_email_deliveries_due", "email_deliveries", ["status", "next_attempt_at"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM email_deliveries)")):
        raise RuntimeError("Email history requires a reviewed export and data transition before downgrade.")
    op.drop_table("email_deliveries")
