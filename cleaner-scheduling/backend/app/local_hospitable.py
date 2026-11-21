from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.db import catalog_session_factory, session_factory
from app.domain.errors import AppError
from app.models import OperatorProfile
from app.models.workspace_connection import WorkspaceConnection
from app.services.availability_email import run_availability_emails
from app.services.cleaner_coordination import process_offer_deadlines
from app.services.email_automation import run_email_automation
from app.services.guest_departure_sync import run_guest_departure_sync
from app.services.hospitable_sync import sync_hospitable
from app.services.local_routing import refresh_local_routing
from app.services.onboarding_email import drain_onboarding_emails
from app.services.security_alerts import drain_security_alerts
from app.workspaces import LEGACY_WORKSPACE_ID, Workspace

FactoriesResult = list[tuple[uuid.UUID, sessionmaker[Session]]]
FactoriesFn = Callable[[], FactoriesResult]


def sync_workspaces(
    factories: FactoriesFn,
    settings: Settings,
    clock: Clock,
    automate_email_fn: Callable[[], None] | None = None,
) -> None:
    """Synchronize workspaces with Hospitable. Testable module-level function.

    Args:
        factories: Function returning list of (workspace_id, session_factory) pairs.
        settings: Application settings.
        clock: Clock instance for time operations.
        automate_email_fn: Optional callback to trigger email automation after sync.
    """
    if not settings.hosted_workspace:
        main()
    try:
        workspace_factories = factories()
    except Exception as error:
        logging.getLogger(__name__).error(
            "hospitable_sync.factories_failed exception=%s", type(error).__name__, exc_info=True
        )
        return
    for identity, factory in workspace_factories:
        try:
            with factory() as session:
                sync_hospitable(
                    session,
                    settings=settings,
                    clock=clock,
                    actor="local-hospitable-owner"
                    if identity == LEGACY_WORKSPACE_ID
                    else f"workspace-worker:{identity}",
                    key=f"poll-{uuid.uuid4()}",
                )
            refresh_local_routing(settings, factory, clock)
        except Exception as error:
            logging.getLogger(__name__).error(
                "workspace_poll_failed workspace_id=%s exception=%s", identity, type(error).__name__, exc_info=True
            )
    if automate_email_fn is not None:
        automate_email_fn()


def automate_emails(
    factories: FactoriesFn,
    settings: Settings,
    clock: Clock,
) -> None:
    """Run email automation for all workspaces. Testable module-level function.

    Args:
        factories: Function returning list of (workspace_id, session_factory) pairs.
        settings: Application settings.
        clock: Clock instance for time operations.
    """
    try:
        workspace_factories = factories()
    except Exception as error:
        logging.getLogger(__name__).error(
            "email_automation.factories_failed exception=%s", type(error).__name__, exc_info=True
        )
        return
    for identity, factory in workspace_factories:
        try:
            with factory() as session:
                process_offer_deadlines(session, settings, clock)
                session.commit()
                connection = session.get(WorkspaceConnection, identity) if identity != LEGACY_WORKSPACE_ID else None
                ready = identity == LEGACY_WORKSPACE_ID or (
                    connection is not None and connection.completed_at is not None
                )
            if not ready:
                continue
            if identity != LEGACY_WORKSPACE_ID:
                invitations = drain_onboarding_emails(factory, settings, clock)
                if invitations.get("accepted", 0) or invitations.get("uncertain", 0) or invitations.get("blocked", 0):
                    logging.getLogger(__name__).info(
                        "onboarding_email_tick workspace_id=%s accepted=%s uncertain=%s blocked=%s",
                        identity,
                        invitations.get("accepted", 0),
                        invitations.get("uncertain", 0),
                        invitations.get("blocked", 0),
                    )
            if not settings.local_email_automation_enabled:
                continue
            if identity != LEGACY_WORKSPACE_ID:
                refresh_local_routing(settings, factory, clock)
            result = run_email_automation(factory, settings, clock)
            logging.getLogger(__name__).info(
                "email_automation_tick workspace_id=%s planned=%s queued=%s accepted=%s uncertain=%s blocked=%s",
                identity,
                result.get("planned", 0),
                result.get("queued", 0),
                result.get("accepted", 0),
                result.get("uncertain", 0),
                result.get("blocked", 0),
            )
        except Exception as error:
            logging.getLogger(__name__).error(
                "workspace_email_failed workspace_id=%s exception=%s",
                identity,
                type(error).__name__,
                exc_info=True,
            )


def sync_guest_departures(
    factories: FactoriesFn,
    settings: Settings,
    clock: Clock,
    on_wake: Callable[[], None] | None = None,
) -> None:
    """Synchronize guest departures for all workspaces. Testable module-level function.

    Args:
        factories: Function returning list of (workspace_id, session_factory) pairs.
        settings: Application settings.
        clock: Clock instance for time operations.
        on_wake: Optional callback to wake the email worker when departures are synced.
    """
    try:
        workspace_factories = factories()
    except Exception as error:
        logging.getLogger(__name__).error(
            "guest_departure.factories_failed exception=%s", type(error).__name__, exc_info=True
        )
        return
    for identity, factory in workspace_factories:
        try:
            if run_guest_departure_sync(factory, settings, clock):
                if on_wake is not None:
                    on_wake()
        except Exception as error:
            logging.getLogger(__name__).error(
                "guest_departure.failed workspace_id=%s exception=%s", identity, type(error).__name__, exc_info=True
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    app.state.wake_automation = lambda: loop.call_soon_threadsafe(wake.set)

    def factories() -> FactoriesResult:
        result: FactoriesResult = (
            [] if app.state.settings.hosted_workspace else [(LEGACY_WORKSPACE_ID, app.state.session_factory)]
        )
        if app.state.settings.workspace_signup_enabled:
            with catalog_session_factory(app.state.settings)() as catalog:
                identities = list(
                    catalog.scalars(
                        select(Workspace.id).where(Workspace.id != LEGACY_WORKSPACE_ID, Workspace.deleted_at.is_(None))
                    )
                )
            for identity in identities:
                if identity not in app.state.workspace_factories:
                    app.state.workspace_factories[identity] = session_factory(app.state.settings, workspace_id=identity)
                factory = app.state.workspace_factories[identity]
                try:
                    with factory() as session:
                        connection = session.get(WorkspaceConnection, identity)
                        if connection is not None:
                            result.append((identity, factory))
                except AppError as error:
                    if error.code != "account_deleted":
                        raise
        return result

    def sync() -> None:
        sync_workspaces(factories, app.state.settings, app.state.clock, automate_email)

    def automate_email() -> None:
        automate_emails(factories, app.state.settings, app.state.clock)

    def wake_email_worker() -> None:
        loop.call_soon_threadsafe(wake.set)

    def sync_departures() -> None:
        sync_guest_departures(factories, app.state.settings, app.state.clock, wake_email_worker)

    async def email_worker() -> None:
        while not stop.is_set():
            wake.clear()
            try:
                await asyncio.to_thread(automate_email)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "email_automation_failed action=review_dashboard exception=%s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.wait_for(wake.wait(), timeout=60)
            except TimeoutError:
                pass

    async def worker() -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(sync)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "hospitable_poll_failed action=review_local_sync exception=%s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=app.state.settings.reservation_reconcile_minutes * 60)
            except TimeoutError:
                pass

    async def availability_worker() -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(run_availability_emails, app.state.settings, app.state.clock)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "availability_email.drain_failed exception=%s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                pass

    async def departure_worker() -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(sync_departures)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "guest_departure.worker_failed exception=%s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                pass

    async def security_worker() -> None:
        while not stop.is_set():
            try:
                await asyncio.to_thread(drain_security_alerts, app.state.settings, app.state.clock)
            except Exception as error:
                logging.getLogger(__name__).error(
                    "security_alert.drain_failed exception=%s", type(error).__name__, exc_info=True
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                pass

    tasks = [asyncio.create_task(worker()), asyncio.create_task(email_worker())]
    if app.state.settings.hospitable_guest_departure_enabled:
        tasks.append(asyncio.create_task(departure_worker()))
    if app.state.settings.local_email_automation_enabled:
        tasks.append(asyncio.create_task(availability_worker()))
    if app.state.settings.security_alert_email_enabled:
        tasks.append(asyncio.create_task(security_worker()))
    try:
        yield
    finally:
        stop.set()
        wake.set()
        await asyncio.gather(*tasks)


def main() -> None:
    from app.config import validate_startup
    from app.db import get_engine, validate_app_role

    settings = get_settings()
    validate_startup(settings)
    validate_app_role(get_engine(settings.database_url))

    if not settings.hospitable_read_only or settings.hosted_workspace:
        raise RuntimeError("Local Hospitable mode is required.")
    now = SystemClock().now()
    with session_factory(settings, LEGACY_WORKSPACE_ID)() as session:
        row = session.scalar(select(OperatorProfile).where(OperatorProfile.auth_subject == "local-hospitable-owner"))
        if row is None:
            session.add(
                OperatorProfile(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    auth_subject="local-hospitable-owner",
                    display_name="Local owner",
                    roles=["owner"],
                    active=True,
                )
            )
        session.commit()
    token = jwt.encode(
        {
            "sub": "local-hospitable-owner",
            "iss": settings.auth_issuer,
            "aud": settings.auth_audience,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=24),
        },
        settings.auth_mock_secret,
        algorithm="HS256",
    )
    path = Path(os.environ["LOCAL_APP_TOKEN_FILE"])
    temporary = path.with_name(f"app-token-{uuid.uuid4()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(token)
    os.replace(temporary, path)


if __name__ == "__main__":
    main()
