"""Scheduling tables for previews and synthetic publication.

Revision ID: 0004_scheduling
Revises: 0003_cleaning_tasks
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_scheduling"
down_revision: str | None = "0003_cleaning_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "cleaning_app"


def upgrade() -> None:
    op.create_table(
        "cleaners",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("phone_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("phone_lookup_hmac", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sms_consent_status", sa.Text(), nullable=False),
        sa.Column("max_jobs", sa.Integer(), nullable=False),
        sa.Column("max_service_minutes", sa.Integer(), nullable=False),
        sa.Column("max_duty_minutes", sa.Integer(), nullable=False),
        sa.Column("default_work_intervals", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_cleaners_version"),
        sa.CheckConstraint("max_jobs > 0", name="ck_cleaners_jobs"),
        sa.CheckConstraint("max_service_minutes > 0 AND max_duty_minutes > 0", name="ck_cleaners_minutes"),
        sa.CheckConstraint(
            "sms_consent_status IN ('pending','opted_in','opted_out')",
            name="ck_cleaners_consent",
        ),
        sa.UniqueConstraint("phone_lookup_hmac", name="uq_cleaners_phone"),
    )
    op.create_table(
        "cleaner_property_eligibility",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("cleaner_id", "property_id", name="uq_cleaner_property_eligibility"),
    )
    op.create_table(
        "cleaner_daily_availability",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("work_intervals", postgresql.JSONB(), nullable=False),
        sa.Column("unavailable_intervals", postgresql.JSONB(), nullable=False),
        sa.Column("origin_label", sa.Text(), nullable=True),
        sa.Column("origin_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("origin_source", sa.Text(), nullable=True),
        sa.Column("max_jobs_override", sa.Integer(), nullable=True),
        sa.Column("max_service_override", sa.Integer(), nullable=True),
        sa.Column("max_duty_override", sa.Integer(), nullable=True),
        sa.Column("assumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "status IN ('available','unavailable','partial')",
            name="ck_cleaner_daily_availability_status",
        ),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("cleaner_id", "service_date", name="uq_cleaner_daily_availability"),
    )
    op.create_table(
        "cleaner_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("flat_rate_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("flat_rate_cents >= 0", name="ck_cleaner_rates_cents"),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "routing_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matrix_digest", sa.Text(), nullable=False),
        sa.Column("points_digest", sa.Text(), nullable=False),
        sa.Column("matrix_seconds", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expires_at > observed_at", name="ck_routing_snapshots_expiry"),
    )
    op.create_table(
        "schedule_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("input_revisions", postgresql.JSONB(), nullable=False),
        sa.Column("solver_version", sa.Text(), nullable=False),
        sa.Column("solver_status", sa.Text(), nullable=False),
        sa.Column("objective_values", postgresql.JSONB(), nullable=False),
        sa.Column("candidate_plan", postgresql.JSONB(), nullable=False),
        sa.Column("uncovered", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("routing_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.Column("day_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('preview','published','superseded','failed')",
            name="ck_schedule_runs_status",
        ),
        sa.ForeignKeyConstraint(["routing_snapshot_id"], ["routing_snapshots.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "schedule_days",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("active_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("policy_version", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["active_run_id"], ["schedule_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("service_date", name="uq_schedule_days_date"),
    )
    op.create_table(
        "cleaner_schedule_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("schedule_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.BigInteger(), nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["schedule_run_id"], ["schedule_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "cleaner_id",
            "service_date",
            "version_number",
            name="uq_cleaner_schedule_versions",
        ),
    )
    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaner_schedule_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("assignment_version", sa.BigInteger(), nullable=False),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("travel_before_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_reason", sa.Text(), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instruction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compensation_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.CheckConstraint("planned_end > planned_start", name="ck_assignments_interval"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_assignments_cents"),
        sa.CheckConstraint(
            "status IN ('planned','published','acknowledged','blocked','completed','superseded','canceled')",
            name="ck_assignments_status",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["cleaning_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["schedule_run_id"], ["schedule_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cleaner_schedule_version_id"], ["cleaner_schedule_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["instruction_id"], ["property_instructions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rate_id"], ["cleaner_rates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["assignments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("task_id", "assignment_version", name="uq_assignments_task_version"),
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_assignments_live_task ON assignments (task_id) "
            "WHERE status IN ('planned','published','acknowledged','blocked','completed')"
        )
    )
    for table in (
        "cleaners",
        "cleaner_property_eligibility",
        "cleaner_daily_availability",
        "cleaner_rates",
        "routing_snapshots",
        "schedule_runs",
        "schedule_days",
        "cleaner_schedule_versions",
        "assignments",
    ):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}"))


def downgrade() -> None:
    for table in (
        "assignments",
        "cleaner_schedule_versions",
        "schedule_days",
        "schedule_runs",
        "routing_snapshots",
        "cleaner_rates",
        "cleaner_daily_availability",
        "cleaner_property_eligibility",
        "cleaners",
    ):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
