"""Foundation tables for audit, inbox, outbox, idempotency, operators, and policy.

Domain tables for reservations, tasks, schedules, SMS, and payments are later milestones.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "cleaning_app"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
            BEGIN
              NEW.updated_at = now();
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'audit_logs is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION policy_settings_immutable() RETURNS trigger AS $$
            BEGIN
              IF OLD.approved_at IS NOT NULL AND NEW.settings IS DISTINCT FROM OLD.settings THEN
                RAISE EXCEPTION 'approved policy settings are immutable';
              END IF;
              IF OLD.approved_at IS NOT NULL AND NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION 'approval timestamp is immutable';
              END IF;
              IF OLD.approved_by IS NOT NULL AND NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
                RAISE EXCEPTION 'approver is immutable';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )

    op.create_table(
        "operator_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("auth_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_operator_profiles_version"),
        sa.CheckConstraint("char_length(auth_subject) > 0", name="ck_operator_profiles_subject"),
        sa.CheckConstraint("char_length(display_name) > 0", name="ck_operator_profiles_name"),
        sa.CheckConstraint("cardinality(roles) > 0", name="ck_operator_profiles_roles_present"),
        sa.CheckConstraint(
            "roles <@ ARRAY['owner','manager','viewer','scheduler']::text[]",
            name="ck_operator_profiles_roles_allowed",
        ),
        sa.UniqueConstraint("auth_subject", name="uq_operator_profiles_auth_subject"),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER operator_profiles_set_updated_at BEFORE UPDATE ON operator_profiles "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("version_number", sa.BigInteger(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("version > 0", name="ck_policy_versions_version"),
        sa.CheckConstraint("version_number > 0", name="ck_policy_versions_number"),
        sa.CheckConstraint("jsonb_typeof(settings) = 'object'", name="ck_policy_versions_settings"),
        sa.CheckConstraint(
            "(NOT is_active) OR approved_at IS NOT NULL",
            name="ck_policy_versions_active_requires_approval",
        ),
        sa.CheckConstraint(
            "("
            "approved_at IS NULL AND approved_by IS NULL AND effective_at IS NULL AND NOT is_active"
            ") OR ("
            "approved_at IS NOT NULL AND approved_by IS NOT NULL AND effective_at IS NOT NULL"
            ")",
            name="ck_policy_versions_approval_shape",
        ),
        sa.UniqueConstraint("version_number", name="uq_policy_versions_number"),
    )
    op.create_index(
        "ix_policy_versions_effective_at",
        "policy_versions",
        ["effective_at"],
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_policy_versions_one_active_approved "
            "ON policy_versions (is_active) "
            "WHERE is_active = true AND approved_at IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER policy_versions_set_updated_at BEFORE UPDATE ON policy_versions "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER policy_versions_immutable_settings BEFORE UPDATE ON policy_versions "
            "FOR EACH ROW EXECUTE FUNCTION policy_settings_immutable()"
        )
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("redacted_changes", postgresql.JSONB(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('operator','anonymous','system')",
            name="ck_audit_logs_actor_type",
        ),
        sa.CheckConstraint("jsonb_typeof(redacted_changes) = 'object'", name="ck_audit_logs_changes"),
        sa.CheckConstraint("char_length(action) > 0", name="ck_audit_logs_action"),
    )
    op.create_index(
        "ix_audit_logs_entity",
        "audit_logs",
        ["entity_type", "entity_id", "occurred_at"],
    )
    op.create_index("ix_audit_logs_correlation", "audit_logs", ["correlation_id"])
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
        )
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_event_id", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("payload_encrypted", postgresql.BYTEA(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_webhook_events_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_webhook_events_attempts"),
        sa.CheckConstraint("char_length(dedupe_key) > 0", name="ck_webhook_events_dedupe"),
        sa.UniqueConstraint("provider", "dedupe_key", name="uq_webhook_events_dedupe"),
    )
    op.create_index("ix_webhook_events_status_received", "webhook_events", ["status", "received_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER webhook_events_set_updated_at BEFORE UPDATE ON webhook_events "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("intent_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_outbox_events_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempts"),
        sa.CheckConstraint("char_length(intent_key) > 0", name="ck_outbox_events_intent"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_outbox_events_payload"),
        sa.UniqueConstraint("intent_key", name="uq_outbox_events_intent"),
    )
    op.execute(
        sa.text("CREATE INDEX ix_outbox_events_available ON outbox_events (available_at) WHERE processed_at IS NULL")
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER outbox_events_set_updated_at BEFORE UPDATE ON outbox_events "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("principal_subject", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_idempotency_records_version"),
        sa.CheckConstraint("char_length(key) BETWEEN 1 AND 128", name="ck_idempotency_records_key"),
        sa.CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_idempotency_records_status",
        ),
        sa.UniqueConstraint(
            "principal_subject",
            "route",
            "key",
            name="uq_idempotency_records_identity",
        ),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])
    op.execute(
        sa.text(
            "CREATE TRIGGER idempotency_records_set_updated_at BEFORE UPDATE ON idempotency_records "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO cleaning_app"))
    for table in (
        "operator_profiles",
        "policy_versions",
        "webhook_events",
        "outbox_events",
        "idempotency_records",
    ):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT ON TABLE audit_logs TO {APP_ROLE}"))
    op.execute(sa.text(f"REVOKE UPDATE, DELETE ON TABLE audit_logs FROM {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT ON TABLE alembic_version TO {APP_ROLE}"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS idempotency_records"))
    op.execute(sa.text("DROP TABLE IF EXISTS outbox_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS webhook_events"))
    op.execute(sa.text("DROP TABLE IF EXISTS audit_logs"))
    op.execute(sa.text("DROP TABLE IF EXISTS policy_versions"))
    op.execute(sa.text("DROP TABLE IF EXISTS operator_profiles"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS policy_settings_immutable()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit_logs_append_only()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS set_updated_at()"))
