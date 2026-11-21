"""Payment preparation, immutable approvals, and rate range protection.

Revision ID: 0006_payments
Revises: 0005_messaging
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006_payments"
down_revision: str | None = "0005_messaging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("ALTER TABLE cleaner_rates ADD CONSTRAINT ck_rate_period CHECK (effective_to > effective_from)")
    op.execute("ALTER TABLE cleaner_rates ADD CONSTRAINT ck_rate_currency CHECK (currency = 'USD')")
    for suffix, scope, predicate in (
        ("default", "cleaner_id WITH =,", "property_id IS NULL"),
        ("property", "cleaner_id WITH =, property_id WITH =,", "property_id IS NOT NULL"),
    ):
        op.execute(
            f"ALTER TABLE cleaner_rates ADD CONSTRAINT ex_rates_{suffix} EXCLUDE USING gist "
            f"({scope} daterange(effective_from, effective_to, '[)') WITH &&) WHERE ({predicate})"
        )
    op.create_table(
        "payment_batches",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("prepared_by", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("exclusions", pg.JSONB(), nullable=False),
        sa.Column("frozen_lines", pg.JSONB(), nullable=True),
        sa.CheckConstraint("period_end > period_start", name="ck_payment_period"),
        sa.CheckConstraint("status IN ('draft','approved','void')", name="ck_payment_status"),
        sa.CheckConstraint("currency = 'USD'", name="ck_payment_currency"),
        sa.CheckConstraint(
            "status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND total_cents >= 0 AND frozen_lines IS NOT NULL)",
            name="ck_payment_approved",
        ),
    )
    op.create_index("ix_payment_queue", "payment_batches", ["status", "period_start"])
    op.create_table(
        "payment_line_items",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", pg.UUID(as_uuid=True), sa.ForeignKey("payment_batches.id"), nullable=False),
        sa.Column("assignment_id", pg.UUID(as_uuid=True), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("task_id", pg.UUID(as_uuid=True), sa.ForeignKey("cleaning_tasks.id"), nullable=False),
        sa.Column("cleaner_id", pg.UUID(as_uuid=True), sa.ForeignKey("cleaners.id"), nullable=False),
        sa.Column("earned_work_key", sa.Text(), nullable=False, unique=True),
        sa.Column("rate_snapshot", pg.JSONB(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("line_type", sa.Text(), nullable=False),
        sa.Column("adjusts_line_id", pg.UUID(as_uuid=True), sa.ForeignKey("payment_line_items.id"), nullable=True),
        sa.Column("adjustment_reason", sa.Text(), nullable=True),
        sa.Column("cleaner_name", sa.Text(), nullable=False),
        sa.Column("property_alias", sa.Text(), nullable=False),
        sa.CheckConstraint("currency = 'USD'", name="ck_payment_line_currency"),
        sa.CheckConstraint(
            "(line_type = 'cleaning' AND amount_cents >= 0 AND adjusts_line_id IS NULL) OR "
            "(line_type = 'adjustment' AND amount_cents != 0 AND adjusts_line_id IS NOT NULL "
            "AND length(trim(adjustment_reason)) > 0)",
            name="ck_payment_line_type",
        ),
    )
    op.create_index("ix_payment_lines_batch", "payment_line_items", ["batch_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_payment_cleaning_task ON payment_line_items (task_id) WHERE line_type = 'cleaning'"
    )
    # Reject edits even through direct SQL; approved financial content is immutable.
    op.execute("""
        CREATE FUNCTION protect_payment_batch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status IN ('approved','void') THEN
            RAISE EXCEPTION 'Finalized payment batch is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Payment history cannot be deleted' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER protect_payment_batch BEFORE UPDATE OR DELETE ON payment_batches
        FOR EACH ROW EXECUTE FUNCTION protect_payment_batch();
        CREATE FUNCTION protect_payment_line() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_status text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Payment history cannot be deleted' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' THEN
            SELECT status INTO target_status FROM payment_batches WHERE id = OLD.batch_id FOR UPDATE;
            IF target_status != 'void' OR
               (to_jsonb(NEW) - 'batch_id' - 'updated_at') != (to_jsonb(OLD) - 'batch_id' - 'updated_at') THEN
              RAISE EXCEPTION 'Only voided lines may move unchanged' USING ERRCODE = '23514';
            END IF;
          END IF;
          SELECT status INTO target_status FROM payment_batches WHERE id = NEW.batch_id FOR UPDATE;
          IF target_status != 'draft' THEN
            RAISE EXCEPTION 'Payment lines require a draft batch' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER protect_payment_line BEFORE INSERT OR UPDATE OR DELETE ON payment_line_items
        FOR EACH ROW EXECUTE FUNCTION protect_payment_line();
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE ON payment_batches, payment_line_items TO cleaning_app")
    op.execute("""
        CREATE FUNCTION protect_cleaner_rate() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Rate history is immutable' USING ERRCODE = '23514';
          END IF;
          IF (to_jsonb(NEW) - 'effective_to') != (to_jsonb(OLD) - 'effective_to') OR
             NEW.effective_to IS NULL OR NEW.effective_to <= OLD.effective_from OR
             (OLD.effective_to IS NOT NULL AND NEW.effective_to >= OLD.effective_to) OR
             EXISTS (SELECT 1 FROM assignments WHERE rate_id = OLD.id AND service_date >= NEW.effective_to) THEN
            RAISE EXCEPTION 'Rate contents and published compensation are immutable' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER protect_cleaner_rate BEFORE UPDATE OR DELETE ON cleaner_rates
        FOR EACH ROW EXECUTE FUNCTION protect_cleaner_rate();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER protect_cleaner_rate ON cleaner_rates")
    op.execute("DROP FUNCTION protect_cleaner_rate()")
    op.drop_table("payment_line_items")
    op.drop_table("payment_batches")
    op.execute("DROP FUNCTION protect_payment_line()")
    op.execute("DROP FUNCTION protect_payment_batch()")
    for name in ("ex_rates_default", "ex_rates_property", "ck_rate_period", "ck_rate_currency"):
        op.execute(f"ALTER TABLE cleaner_rates DROP CONSTRAINT {name}")
