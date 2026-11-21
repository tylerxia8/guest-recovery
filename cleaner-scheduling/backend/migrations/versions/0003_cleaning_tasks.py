"""Cleaning task derivation. Prior migrations stay unchanged.

Revision ID: 0003_cleaning_tasks
Revises: 0002_reservations
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_cleaning_tasks"
down_revision: str | None = "0002_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "cleaning_app"


def upgrade() -> None:
    op.create_table(
        "cleaning_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="departure"),
        sa.Column("next_reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("release_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manager_target_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("duration_source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_actor", sa.Text(), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_cleaning_tasks_version"),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_cleaning_tasks_duration",
        ),
        sa.CheckConstraint("kind = 'departure'", name="ck_cleaning_tasks_kind"),
        sa.CheckConstraint(
            "status IN ('pending_deadline','ready','assigned','completed','canceled')",
            name="ck_cleaning_tasks_status",
        ),
        sa.CheckConstraint(
            "duration_source IN ('property_override','bedroom_default','missing')",
            name="ck_cleaning_tasks_duration_source",
        ),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_reservation_id"], ["reservations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["next_reservation_id"], ["reservations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_reservation_id", "kind", name="uq_cleaning_tasks_source_kind"),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_cleaning_tasks_deadline ON cleaning_tasks (deadline_at) "
            "WHERE status NOT IN ('completed','canceled')"
        )
    )
    op.create_index("ix_cleaning_tasks_status", "cleaning_tasks", ["status"])
    op.execute(
        sa.text(
            "CREATE TRIGGER cleaning_tasks_set_updated_at BEFORE UPDATE ON cleaning_tasks "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )
    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE cleaning_tasks TO {APP_ROLE}"))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS cleaning_tasks"))
