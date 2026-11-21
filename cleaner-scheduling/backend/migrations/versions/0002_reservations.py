"""Reservation ingestion tables. Foundation migration 0001 stays unchanged.

Revision ID: 0002_reservations
Revises: 0001_foundation
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_reservations"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "cleaning_app"


def upgrade() -> None:
    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/New_York"),
        sa.Column("bedrooms", sa.SmallInteger(), nullable=False),
        sa.Column("duration_override_minutes", sa.Integer(), nullable=True),
        sa.Column("location_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("geocode_status", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_properties_version"),
        sa.CheckConstraint("bedrooms >= 0", name="ck_properties_bedrooms"),
        sa.CheckConstraint(
            "duration_override_minutes IS NULL OR duration_override_minutes > 0",
            name="ck_properties_duration",
        ),
        sa.CheckConstraint("timezone = 'America/New_York'", name="ck_properties_timezone"),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="ck_properties_coordinates",
        ),
        sa.UniqueConstraint("provider", "external_id", name="uq_properties_external"),
    )
    op.create_index("ix_properties_active", "properties", ["active"])
    op.execute(
        sa.text(
            "CREATE TRIGGER properties_set_updated_at BEFORE UPDATE ON properties "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "reservation_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("snapshot_id", sa.Text(), nullable=True),
        sa.Column("snapshot_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("deletion_inference", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("version > 0", name="ck_reservation_sync_runs_version"),
        sa.CheckConstraint("rows_seen >= 0 AND rows_applied >= 0", name="ck_reservation_sync_runs_counts"),
        sa.CheckConstraint(
            "mode IN ('mock','api','calendar','import')",
            name="ck_reservation_sync_runs_mode",
        ),
        sa.CheckConstraint(
            "strategy IN ('incremental','full')",
            name="ck_reservation_sync_runs_strategy",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed')",
            name="ck_reservation_sync_runs_status",
        ),
        sa.UniqueConstraint("provider", "idempotency_key", name="uq_reservation_sync_runs_idempotency"),
    )
    op.create_index(
        "ix_reservation_sync_runs_started",
        "reservation_sync_runs",
        ["provider", sa.text("started_at DESC")],
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER reservation_sync_runs_set_updated_at BEFORE UPDATE ON reservation_sync_runs "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checkin_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkout_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timestamp_provenance", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("last_sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_reservations_version"),
        sa.CheckConstraint("checkout_at > checkin_at", name="ck_reservations_interval"),
        sa.CheckConstraint("status IN ('active','canceled')", name="ck_reservations_status"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_sync_run_id"], ["reservation_sync_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("provider", "external_id", name="uq_reservations_external"),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_reservations_property_checkin_active ON reservations (property_id, checkin_at) "
            "WHERE status = 'active'"
        )
    )
    op.create_index("ix_reservations_property_checkout", "reservations", ["property_id", "checkout_at"])
    op.create_index("ix_reservations_observed_at", "reservations", ["observed_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER reservations_set_updated_at BEFORE UPDATE ON reservations "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "property_instructions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("source_fields", postgresql.JSONB(), nullable=False),
        sa.Column("local_overrides", postgresql.JSONB(), nullable=False),
        sa.Column("source_secret_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("local_secret_ciphertext", postgresql.BYTEA(), nullable=True),
        sa.Column("effective_encrypted", postgresql.BYTEA(), nullable=False),
        sa.Column("effective_digest", sa.Text(), nullable=False),
        sa.Column("instruction_version", sa.BigInteger(), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_subject", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.CheckConstraint("instruction_version > 0", name="ck_property_instructions_version"),
        sa.CheckConstraint(
            "jsonb_typeof(source_fields) = 'object' AND jsonb_typeof(local_overrides) = 'object'",
            name="ck_property_instructions_json",
        ),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["property_instructions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("property_id", "instruction_version", name="uq_property_instructions_version"),
    )
    op.create_index(
        "ix_property_instructions_property_created",
        "property_instructions",
        ["property_id", sa.text("created_at DESC")],
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION property_instructions_append_only() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'property_instructions is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER property_instructions_no_update BEFORE UPDATE OR DELETE ON property_instructions "
            "FOR EACH ROW EXECUTE FUNCTION property_instructions_append_only()"
        )
    )

    op.create_table(
        "exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_subject", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_exceptions_version"),
        sa.CheckConstraint("severity IN ('info','warning','critical')", name="ck_exceptions_severity"),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved')",
            name="ck_exceptions_status",
        ),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_exceptions_details"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["reservation_sync_runs.id"], ondelete="RESTRICT"),
        # task_id and assignment_id gain foreign keys when those tables exist.
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_exceptions_open_dedupe ON exceptions (dedupe_key) "
            "WHERE status IN ('open','acknowledged')"
        )
    )
    op.create_index("ix_exceptions_status_opened", "exceptions", ["status", "severity", "opened_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER exceptions_set_updated_at BEFORE UPDATE ON exceptions "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "reservation_import_previews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("valid_rows", postgresql.JSONB(), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_reservation_import_previews_version"),
        sa.CheckConstraint(
            "status IN ('preview','committed','expired')",
            name="ck_reservation_import_previews_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(valid_rows) = 'array' AND jsonb_typeof(errors) = 'array'",
            name="ck_reservation_import_previews_json",
        ),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER reservation_import_previews_set_updated_at BEFORE UPDATE ON "
            "reservation_import_previews FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    for table in (
        "properties",
        "reservation_sync_runs",
        "reservations",
        "exceptions",
        "reservation_import_previews",
    ):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT ON TABLE property_instructions TO {APP_ROLE}"))
    op.execute(sa.text(f"REVOKE UPDATE, DELETE ON TABLE property_instructions FROM {APP_ROLE}"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS reservation_import_previews"))
    op.execute(sa.text("DROP TABLE IF EXISTS reservations"))
    op.execute(sa.text("DROP TABLE IF EXISTS property_instructions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS property_instructions_append_only()"))
    op.execute(sa.text("DROP TABLE IF EXISTS exceptions"))
    op.execute(sa.text("DROP TABLE IF EXISTS reservation_sync_runs"))
    op.execute(sa.text("DROP TABLE IF EXISTS properties"))
