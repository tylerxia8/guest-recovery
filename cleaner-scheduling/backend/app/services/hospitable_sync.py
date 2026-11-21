from __future__ import annotations

import json
import logging
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.errors import AdapterError
from app.adapters.reservations.hospitable_read import HospitableReadClient
from app.clock import Clock
from app.config import Settings, validate_startup
from app.domain.errors import AppError
from app.models import OperationalException, Property, Reservation, ReservationSyncRun
from app.models.workspace_connection import WorkspaceConnection
from app.services.audit import write_audit
from app.services.field_cipher import protect_text, reveal_text
from app.services.idempotency import request_digest, resolve_idempotency, store_idempotency
from app.services.reservation_sync import _open_exception, apply_normalized
from app.services.task_generation import recompute_property_tasks
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID

logger = logging.getLogger(__name__)


def sync_hospitable(session: Session, *, settings: Settings, clock: Clock, actor: str, key: str) -> dict[str, object]:
    validate_startup(settings)
    if not settings.hospitable_read_only:
        raise AppError("unsupported_capability", "Read-only Hospitable sync is not enabled.", 422)
    from app.services.workspace_lock import try_acquire_workspace_lock

    if not try_acquire_workspace_lock(session, get_workspace_id(session), "reservation-sync"):
        raise AppError("sync_already_running", "A read-only Hospitable sync is already running.", 409)
    route = "/v1/reservation-syncs"
    digest = request_digest({"provider": "hospitable", "mode": "full"})
    resolution = resolve_idempotency(session, principal_subject=actor, route=route, key=key, digest=digest, clock=clock)
    if resolution.outcome == "replay" and resolution.body is not None:
        return resolution.body
    if resolution.outcome == "conflict":
        raise AppError("idempotency_conflict", "Idempotency-Key was reused.", 409)
    now = clock.now()
    run = ReservationSyncRun(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider="hospitable",
        mode="api",
        strategy="full",
        status="running",
        started_at=now,
        rows_seen=0,
        rows_applied=0,
        idempotency_key=key,
        snapshot_complete=False,
        deletion_inference=False,
    )
    session.add(run)
    session.flush()
    try:
        tracked = list(session.scalars(select(Reservation.external_id).where(Reservation.provider == "hospitable")))
        if len(tracked) > 10000:
            raise AdapterError("Tracked reservation limit requires review.")
        workspace_id = session.info.get("workspace_id", LEGACY_WORKSPACE_ID)
        token = None
        if workspace_id != LEGACY_WORKSPACE_ID:
            connection = session.get(WorkspaceConnection, workspace_id)
            if connection is None:
                raise AdapterError("Connect this workspace to Hospitable first.")
            token = reveal_text(connection.token_ciphertext, settings.field_encryption_secret)
        client = HospitableReadClient(settings.hospitable_token_file, access_token=token)
        try:
            houses, reservations, rejections, unbooked = client.snapshot(
                now.astimezone(ZoneInfo("America/New_York")).date(), tracked
            )
        finally:
            client.close()
    except (AdapterError, OSError, ValueError):
        run.status = "failed"
        run.error_code = "hospitable_read_failed"
        _open_exception(
            session,
            clock=clock,
            code="PROVIDER_UNAVAILABLE",
            dedupe_key="provider-unavailable:hospitable",
            sync_run_id=run.id,
            details={"error": run.error_code},
            severity="critical",
        )
    else:
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        for house in houses:
            prop = session.scalar(
                select(Property).where(Property.provider == "hospitable", Property.external_id == house.external_id)
            )
            if prop is None:
                prop = Property(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    version=1,
                    provider="hospitable",
                    external_id=house.external_id,
                    name=house.name,
                    bedrooms=house.bedrooms,
                    timezone="America/New_York",
                    location_ciphertext=b"",
                    geocode_status="unverified",
                    active=True,
                )
                session.add(prop)
            elif (prop.name, prop.bedrooms) != (house.name, house.bedrooms):
                prop.name, prop.bedrooms = house.name, house.bedrooms
                prop.version += 1
                prop.updated_at = now
            if settings.local_free_routing:
                location = (
                    ""
                    if house.coordinates is None and house.display_address is None
                    else json.dumps(
                        {
                            "coordinates": house.coordinates,
                            "display_address": house.display_address,
                            "unit": house.unit,
                            "source": "hospitable",
                        },
                        sort_keys=True,
                    )
                )
                old = (
                    reveal_text(prop.location_ciphertext, settings.field_encryption_secret)
                    if prop.location_ciphertext
                    else ""
                )
                if old != location:
                    prop.location_ciphertext = (
                        protect_text(location, settings.field_encryption_secret) if location else b""
                    )
                    prop.latitude = prop.longitude = None
                    prop.geocode_status = "source_coordinates" if house.coordinates is not None else "unverified"
                    prop.version += 1
                    prop.updated_at = clock.now()
            session.flush()
            recompute_property_tasks(session, clock=clock, property_id=prop.id)
        for record in reservations:
            run.rows_applied += int(apply_normalized(session, clock=clock, run=run, record=record))
        for code, identity in set(rejections):
            _open_exception(
                session,
                clock=clock,
                code=code,
                dedupe_key=f"{code}:{identity}",
                sync_run_id=run.id,
                details={"source_id": identity},
                severity="warning",
            )
        recovered = {"provider-unavailable:hospitable"}
        recovered.update(f"HOSPITABLE_PROPERTY_REVIEW:{house.external_id}" for house in houses)
        recovered.update(f"HOSPITABLE_RESERVATION_REVIEW:{record.external_id}" for record in reservations)
        recovered.update(f"HOSPITABLE_RESERVATION_REVIEW:{identity}" for identity in unbooked)
        for exception in session.scalars(
            select(OperationalException).where(
                OperationalException.dedupe_key.in_(recovered),
                OperationalException.status.in_(("open", "acknowledged")),
            )
        ):
            exception.status = "resolved"
            exception.resolved_at = clock.now()
            exception.resolution_reason = "Source read succeeded and the previously rejected facts validated."
            exception.updated_at = clock.now()
            exception.version += 1
        run.rows_seen = len(reservations) + len(set(rejections)) + len(unbooked)
        run.status = "partial" if rejections else "succeeded"
        run.source_as_of = now
        run.snapshot_complete = False
    run.finished_at = clock.now()
    run.version += 1
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=actor,
        action="hospitable.read_sync",
        entity_type="reservation_sync_run",
        entity_id=run.id,
        correlation_id=run.id,
        reason=run.status,
        redacted_changes={
            "status": run.status,
            "rows_seen": run.rows_seen,
            "rows_applied": run.rows_applied,
            "upstream_writes": 0,
        },
        request_id=None,
    )
    response: dict[str, object] = {
        "job_id": str(run.id),
        "status": run.status,
        "status_url": f"/v1/reservation-syncs/{run.id}",
        "rows_seen": run.rows_seen,
        "rows_applied": run.rows_applied,
        "upstream_writes": 0,
        "error_code": run.error_code,
    }
    store_idempotency(
        session,
        settings=settings,
        clock=clock,
        principal_subject=actor,
        route=route,
        key=key,
        digest=digest,
        status=200,
        body=response,
        resource_id=run.id,
    )
    session.commit()
    logger.info(
        "hospitable_read_sync status=%s rows_seen=%s rows_applied=%s run_id=%s",
        run.status,
        run.rows_seen,
        run.rows_applied,
        run.id,
    )
    return response
