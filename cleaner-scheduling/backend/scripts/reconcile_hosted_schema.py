"""Reconcile the hosted Cleaner migration branch with the repository branch."""

import argparse
import importlib.util
import os
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection

OLD_REVISION = "0030_stay_connections"
NEW_REVISION = "0034_legacy_return_lookup"
VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
REPO_UPGRADES = (
    "0029_stay_times",
    "0030_manager_invitations",
    "0031_action_inbox_claims",
    "0032_account_workspaces",
    "0033_manager_session_scope",
    NEW_REVISION,
)


def check_schema(connection: Connection) -> str:
    revisions = connection.scalars(sa.text("SELECT version_num FROM alembic_version")).all()
    if revisions == [NEW_REVISION]:
        return NEW_REVISION
    if revisions != [OLD_REVISION]:
        raise RuntimeError(f"Expected the hosted starting revision, got {revisions!r}.")
    inspector = sa.inspect(connection)
    tasks = {column["name"] for column in inspector.get_columns("cleaning_tasks")}
    sessions = {column["name"] for column in inspector.get_columns("manager_sessions")}
    if (
        not {"external_return_request_id", "return_scope", "return_request_digest"} <= tasks
        or "guest_scope" in tasks
        or "scope" not in sessions
        or not inspector.has_table("stay_adjustments")
    ):
        raise RuntimeError("The database does not match the known hosted Cleaner schema.")
    return OLD_REVISION


def reconcile(connection: Connection) -> None:
    connection.execute(sa.text("SET LOCAL row_security = off"))
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(sa.text("LOCK TABLE alembic_version IN EXCLUSIVE MODE"))
    if check_schema(connection) == NEW_REVISION:
        return

    with Operations.context(MigrationContext.configure(connection)) as operations:
        operations.add_column("cleaning_tasks", sa.Column("guest_scope", sa.Text(), nullable=True))
        operations.drop_constraint("ck_cleaning_tasks_kind", "cleaning_tasks", type_="check")
        operations.create_check_constraint(
            "ck_cleaning_tasks_kind",
            "cleaning_tasks",
            "(kind IN ('departure', 'return') AND guest_scope IS NULL) OR "
            "(kind = 'guest_touchup' AND guest_scope IS NOT NULL AND deadline_at IS NOT NULL)",
        )
        for revision in REPO_UPGRADES:
            spec = importlib.util.spec_from_file_location(revision, VERSIONS / f"{revision}.py")
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Missing repository migration {revision}.")
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.upgrade()

    # Retain the historical records and transfer approvals matching the current booking.
    connection.execute(
        sa.text("""
        WITH current_approval AS (
            SELECT DISTINCT ON (a.workspace_id, a.reservation_id, a.kind)
                a.workspace_id, a.reservation_id, a.kind, a.target_at
            FROM stay_adjustments a
            JOIN reservations r
                ON r.workspace_id=a.workspace_id AND r.id=a.reservation_id AND r.property_id=a.property_id
            WHERE r.status='active' AND a.invalidated_at IS NULL AND a.decision='approved'
              AND a.source_checkin_at=r.checkin_at AND a.source_checkout_at=r.checkout_at
              AND ((a.kind='early_checkin' AND a.target_at<r.checkin_at)
                OR (a.kind='late_checkout' AND a.target_at>r.checkout_at))
            ORDER BY a.workspace_id, a.reservation_id, a.kind, a.updated_at DESC, a.id
        ), changes AS (
            SELECT workspace_id, reservation_id,
                max(target_at) FILTER (WHERE kind='early_checkin') AS early,
                max(target_at) FILTER (WHERE kind='late_checkout') AS late
            FROM current_approval GROUP BY workspace_id,reservation_id
        )
        UPDATE reservations r SET approved_guest_checkin_at=c.early, approved_guest_checkout_at=c.late
        FROM changes c WHERE r.workspace_id=c.workspace_id AND r.id=c.reservation_id
        """)
    )
    connection.execute(
        sa.text("UPDATE alembic_version SET version_num=:target WHERE version_num=:source"),
        {"target": NEW_REVISION, "source": OLD_REVISION},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Check the schema inside a read-only transaction.")
    mode.add_argument("--dry-run", action="store_true", help="Exercise the migration and roll back.")
    mode.add_argument("--apply", action="store_true", help="Commit the reconciliation.")
    args = parser.parse_args()
    engine = sa.create_engine(os.environ["MIGRATION_DATABASE_URL"])
    with engine.connect() as connection, connection.begin() as transaction:
        if args.check:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            print(f"Cleaner schema check passed at {check_schema(connection)}.")
            return
        reconcile(connection)
        if args.dry_run:
            transaction.rollback()
    if args.dry_run:
        print(f"Cleaner transition to {NEW_REVISION} passed and was rolled back.")
    else:
        print(f"Cleaner schema is at {NEW_REVISION}.")


if __name__ == "__main__":
    main()
