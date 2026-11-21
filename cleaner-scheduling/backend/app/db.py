from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.config import Settings
from app.domain.errors import AppError, ConfigError
from app.schema_revision import EXPECTED_SCHEMA_REVISION

logger = logging.getLogger(__name__)

_engines: dict[str, Engine] = {}


def validate_app_role(engine: Engine) -> None:
    """Validate the database role meets security requirements.

    Fails closed: refuses to start if any check fails.
    - current_user must be exactly cleaning_app
    - rolsuper must be false
    - rolbypassrls must be false
    - rolcreaterole must be false
    - current_user must not own any RLS table (workspace_id column)
    - schema revision must match
    - all tenant tables must have FORCE ROW LEVEL SECURITY

    Logs a single startup line with current_user, rolsuper, rolbypassrls at WARNING.
    """
    with engine.connect() as connection:
        role_info = (
            connection.execute(
                text(
                    "SELECT current_user AS role_name, rolsuper, rolbypassrls, rolcreaterole, rolinherit "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
            .mappings()
            .first()
        )

        if role_info is None:
            raise ConfigError("app_role_invalid", "Database role does not exist.")

        role_name = role_info["role_name"]
        is_super = role_info["rolsuper"]
        bypasses_rls = role_info["rolbypassrls"]
        can_create_role = role_info["rolcreaterole"]
        can_inherit = role_info["rolinherit"]

        logger.warning(
            "db.validate_app_role current_user=%s rolsuper=%s rolbypassrls=%s rolcreaterole=%s rolinherit=%s",
            role_name,
            is_super,
            bypasses_rls,
            can_create_role,
            can_inherit,
        )

        if role_name != "cleaning_app":
            raise ConfigError(
                "app_role_not_cleaning_app",
                f"Application must connect as cleaning_app. current_user={role_name}",
            )

        if is_super:
            raise ConfigError(
                "app_role_superuser",
                f"Application must not connect as superuser. current_user={role_name}",
            )

        if bypasses_rls:
            raise ConfigError(
                "app_role_bypassrls",
                f"Application role must not have BYPASSRLS. current_user={role_name}",
            )

        if can_create_role:
            raise ConfigError(
                "app_role_createrole",
                f"Application role must not have CREATEROLE. current_user={role_name}",
            )

        owns_rls_table = connection.scalar(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_class c "
                "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                "  JOIN pg_attribute a ON a.attrelid = c.oid "
                "    AND a.attname = 'workspace_id' AND NOT a.attisdropped "
                "  WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "    AND pg_has_role(current_user, c.relowner, 'MEMBER')"
                ")"
            )
        )
        if owns_rls_table:
            raise ConfigError(
                "app_role_owns_table",
                f"Application role must not own RLS tables. current_user={role_name}",
            )

        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != EXPECTED_SCHEMA_REVISION:
            raise ConfigError(
                "schema_revision_mismatch",
                f"Expected schema {EXPECTED_SCHEMA_REVISION}, got {revision}.",
            )

        missing_force_rls = connection.scalar(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_class c "
                "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                "  JOIN pg_attribute a ON a.attrelid = c.oid "
                "    AND a.attname = 'workspace_id' AND NOT a.attisdropped "
                "  WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "    AND c.relname != 'manager_accounts' "
                "    AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)"
                ")"
            )
        )
        if missing_force_rls:
            raise ConfigError(
                "force_rls_missing",
                "All tenant tables must have FORCE ROW LEVEL SECURITY enabled.",
            )


@event.listens_for(Session, "after_begin")
def set_workspace_context(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    workspace_id = session.info.get("workspace_id")
    if workspace_id is not None and not isinstance(workspace_id, UUID):
        raise ValueError("Session workspace_id must be a UUID")
    if workspace_id is not None:
        active = connection.scalar(
            text("SELECT deleted_at IS NULL FROM workspaces WHERE id = :id FOR SHARE"),
            {"id": workspace_id},
        )
        if active is not True:
            raise AppError("account_deleted", "This workspace is no longer available.", 401)
    connection.execute(
        text("SELECT set_config('app.workspace_id', :workspace_id, true)"),
        {"workspace_id": str(workspace_id) if workspace_id is not None else ""},
    )


def get_engine(url: str) -> Engine:
    engine = _engines.get(url)
    if engine is None:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000 -c lock_timeout=5000"},
        )
        _engines[url] = engine
    return engine


def session_factory(settings: Settings, workspace_id: UUID) -> sessionmaker[Session]:
    if not isinstance(workspace_id, UUID):
        raise ValueError("workspace_id must be a UUID")
    return sessionmaker(
        bind=get_engine(settings.database_url), expire_on_commit=False, info={"workspace_id": workspace_id}
    )


def catalog_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(settings.database_url), expire_on_commit=False)


def dispose_engines() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
