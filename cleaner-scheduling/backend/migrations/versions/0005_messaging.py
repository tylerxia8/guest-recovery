"""Mock SMS messages, acknowledgments, and scoped cleaner links.

Revision ID: 0005_messaging
Revises: 0004_scheduling
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_messaging"
down_revision: str | None = "0004_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "cleaning_app"


def upgrade() -> None:
    op.add_column("cleaners", sa.Column("consent_evidence_ref", sa.Text(), nullable=True))
    op.add_column("cleaners", sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "sms_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cleaner_schedule_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("intent_key", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("body_encrypted", postgresql.BYTEA(), nullable=True),
        sa.Column("body_digest", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_rank", sa.Integer(), nullable=False),
        sa.Column("provider_status_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=True),
        sa.Column("schedule_version_number", sa.BigInteger(), nullable=True),
        sa.Column("part_number", sa.Integer(), nullable=True),
        sa.Column("part_count", sa.Integer(), nullable=True),
        sa.CheckConstraint("direction IN ('inbound','outbound')", name="ck_sms_direction"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_sms_attempts"),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cleaner_schedule_version_id"],
            ["cleaner_schedule_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.execute(sa.text("CREATE UNIQUE INDEX uq_sms_intent ON sms_messages (intent_key) WHERE intent_key IS NOT NULL"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_sms_provider_message ON sms_messages (provider, provider_message_id) "
            "WHERE provider_message_id IS NOT NULL"
        )
    )
    op.create_table(
        "acknowledgments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cleaner_schedule_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sms_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_subject", sa.Text(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.CheckConstraint("method IN ('sms','manager_recorded')", name="ck_ack_method"),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cleaner_schedule_version_id"],
            ["cleaner_schedule_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["sms_message_id"], ["sms_messages.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("cleaner_schedule_version_id", name="uq_ack_version"),
    )
    op.create_table(
        "cleaner_access_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleaner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["cleaner_id"], ["cleaners.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_cleaner_access_token"),
    )
    for table in ("sms_messages", "acknowledgments", "cleaner_access_links"):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS cleaner_access_links"))
    op.execute(sa.text("DROP TABLE IF EXISTS acknowledgments"))
    op.execute(sa.text("DROP TABLE IF EXISTS sms_messages"))
    op.drop_column("cleaners", "opted_out_at")
    op.drop_column("cleaners", "consent_evidence_ref")
