from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.errors import AdapterError, CapabilityUnavailable, ProviderUnavailable
from app.adapters.reservations.hospitable import HospitableReservationProvider
from app.adapters.reservations.mock import MockReservationProvider
from app.adapters.types import NormalizedReservation
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models import CleaningTask, OperationalException, Property, Reservation, ReservationSyncRun
from app.services.audit import write_audit
from app.services.schedule_freshness import bump_task
from app.services.task_generation import recompute_property_tasks

_MODE = {"mock": "mock", "api": "api", "calendar": "calendar"}


def canonical_digest(record: NormalizedReservation) -> str:
    payload = {
        "checkin_at": record.checkin_at.isoformat(),
        "checkout_at": record.checkout_at.isoformat(),
        "external_id": record.external_id,
        "property_external_id": record.property_external_id,
        "provider": record.provider,
        "source_revision": record.source_revision,
        "source_updated_at": record.source_updated_at.isoformat() if record.source_updated_at else None,
        "status": record.status,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def latest_cursor(session: Session, provider: str) -> str | None:
    run = session.scalar(
        select(ReservationSyncRun)
        .where(ReservationSyncRun.provider == provider, ReservationSyncRun.cursor_after.is_not(None))
        .order_by(ReservationSyncRun.started_at.desc())
        .limit(1)
    )
    if run is None:
        return None
    if run.status in {"partial", "failed"}:
        return run.cursor_before
    return run.cursor_after


def running_sync(session: Session, provider: str) -> ReservationSyncRun | None:
    return session.scalar(
        select(ReservationSyncRun).where(
            ReservationSyncRun.provider == provider,
            ReservationSyncRun.status.in_(("queued", "running")),
        )
    )


def create_sync_run(
    session: Session,
    *,
    clock: Clock,
    provider: str,
    strategy: str,
    idempotency_key: str,
) -> ReservationSyncRun:
    if provider not in _MODE:
        raise AppError("unsupported_capability", "Reservation provider is not supported.", 422)
    if provider == "api":
        raise AppError(
            "unsupported_capability",
            "Hospitable API ingestion is not verified and stays disabled.",
            422,
        )
    if provider == "calendar":
        raise AppError(
            "unsupported_capability",
            "Calendar feed fetch is scaffolded and does not run a live sync.",
            422,
        )
    existing = running_sync(session, provider)
    if existing is not None:
        raise AppError(
            "sync_already_running",
            "A reservation sync is already running.",
            409,
            {"job_id": str(existing.id), "status_url": f"/v1/reservation-syncs/{existing.id}"},
        )
    now = clock.now()
    run = ReservationSyncRun(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        provider=provider,
        mode=_MODE[provider],
        strategy=strategy,
        status="queued",
        started_at=now,
        finished_at=None,
        cursor_before=None if strategy == "full" else latest_cursor(session, provider),
        cursor_after=None,
        snapshot_id=None,
        snapshot_complete=False,
        source_as_of=None,
        rows_seen=0,
        rows_applied=0,
        error_code=None,
        idempotency_key=idempotency_key,
        deletion_inference=False,
    )
    session.add(run)
    session.flush()
    return run


def execute_sync(session: Session, *, clock: Clock, sync_run_id: uuid.UUID) -> ReservationSyncRun:
    run = session.get(ReservationSyncRun, sync_run_id)
    if run is None:
        raise AppError("not_found", "Reservation sync was not found.", 404)
    if run.status in {"succeeded", "partial", "failed"}:
        return run
    run.status = "running"
    provider = MockReservationProvider()
    cursor = run.cursor_before
    seen = 0
    applied = 0
    try:
        while True:
            page = provider.pull(cursor)
            for code, external_id in page.rejections:
                _open_exception(
                    session,
                    clock=clock,
                    code=code.upper(),
                    dedupe_key=f"{code}:{external_id}",
                    sync_run_id=run.id,
                    details={"external_id": external_id},
                    severity="warning",
                )
                seen += 1
            for record in page.records:
                seen += 1
                if _apply_record(session, clock, run, record):
                    applied += 1
            run.rows_seen = seen
            run.rows_applied = applied
            run.snapshot_id = page.snapshot_id
            run.source_as_of = page.source_as_of
            run.cursor_after = page.next_cursor if page.next_cursor is not None else f"end:{page.snapshot_id or 'done'}"
            run.snapshot_complete = page.complete
            run.deletion_inference = False
            session.flush()
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        run.status = "succeeded"
        run.error_code = None
    except (ProviderUnavailable, CapabilityUnavailable, AdapterError) as exc:
        run.status = "partial" if seen else "failed"
        run.error_code = exc.code
        run.snapshot_complete = False
        _open_exception(
            session,
            clock=clock,
            code="PROVIDER_UNAVAILABLE",
            dedupe_key=f"provider-unavailable:{run.provider}",
            sync_run_id=run.id,
            details={"error": exc.code},
            severity="critical",
        )
    run.finished_at = clock.now()
    run.version += 1
    session.flush()
    return run


def publication_blocked(session: Session, *, clock: Clock, settings: Settings) -> bool:
    run = session.scalar(
        select(ReservationSyncRun)
        .where(ReservationSyncRun.provider == settings.reservation_provider)
        .order_by(ReservationSyncRun.started_at.desc(), ReservationSyncRun.id.desc())
        .limit(1)
    )
    if run is None or run.status != "succeeded" or run.source_as_of is None:
        return True
    age = clock.now() - run.source_as_of
    return age < timedelta(0) or age > timedelta(minutes=settings.integration_stale_threshold_minutes)


def apply_normalized(
    session: Session,
    *,
    clock: Clock,
    run: ReservationSyncRun | None,
    record: NormalizedReservation,
) -> bool:
    return _apply_record(session, clock, run, record)


def _apply_record(
    session: Session,
    clock: Clock,
    run: ReservationSyncRun | None,
    record: NormalizedReservation,
) -> bool:
    prop = session.scalar(
        select(Property).where(
            Property.provider == record.provider,
            Property.external_id == record.property_external_id,
            Property.active.is_(True),
        )
    )
    if prop is None:
        _open_exception(
            session,
            clock=clock,
            code="UNMAPPED_PROPERTY",
            dedupe_key=f"unmapped:{record.provider}:{record.property_external_id}",
            sync_run_id=None if run is None else run.id,
            details={"property_external_id": record.property_external_id},
            severity="warning",
        )
        return False
    current = session.scalar(
        select(Reservation).where(
            Reservation.provider == record.provider,
            Reservation.external_id == record.external_id,
        )
    )
    digest = canonical_digest(record)
    now = clock.now()
    if current is None:
        session.add(
            Reservation(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                property_id=prop.id,
                provider=record.provider,
                external_id=record.external_id,
                status=record.status,
                checkin_at=record.checkin_at,
                checkout_at=record.checkout_at,
                source_revision=record.source_revision,
                source_updated_at=record.source_updated_at,
                observed_at=record.observed_at,
                timestamp_provenance=record.timestamp_provenance,
                payload_digest=digest,
                last_sync_run_id=None if run is None else run.id,
            )
        )
        session.flush()
        _overlap_exception(session, clock, prop.id, record)
        recompute_property_tasks(session, clock=clock, property_id=prop.id)
        return True
    if current.payload_digest == digest:
        recompute_property_tasks(session, clock=clock, property_id=prop.id)
        return False
    if not _is_newer(record, current):
        write_audit(
            session,
            clock=clock,
            actor_type="system",
            actor_subject="reservation-sync",
            action="reservation.stale_revision_ignored",
            entity_type="reservation",
            entity_id=current.id,
            correlation_id=uuid.uuid4() if run is None else run.id,
            reason="older source revision was ignored",
            redacted_changes={"external_id": record.external_id, "source_revision": record.source_revision},
            request_id=None,
        )
        _open_exception(
            session,
            clock=clock,
            code="STALE_REVISION_IGNORED",
            dedupe_key=f"stale:{record.provider}:{record.external_id}:{record.source_revision}",
            sync_run_id=None if run is None else run.id,
            details={"external_id": record.external_id, "source_revision": record.source_revision},
            severity="info",
        )
        return False
    arrival_changed = current.checkin_at != record.checkin_at
    current.status = record.status
    current.checkin_at = record.checkin_at
    current.checkout_at = record.checkout_at
    current.source_revision = record.source_revision
    current.source_updated_at = record.source_updated_at
    current.observed_at = record.observed_at
    current.timestamp_provenance = record.timestamp_provenance
    current.payload_digest = digest
    current.property_id = prop.id
    current.last_sync_run_id = None if run is None else run.id
    current.version += 1
    current.updated_at = now
    session.flush()
    if record.status == "active":
        _overlap_exception(session, clock, prop.id, record)
    recompute_property_tasks(session, clock=clock, property_id=prop.id)
    if arrival_changed:
        for task in session.scalars(
            select(CleaningTask).where(
                CleaningTask.next_reservation_id == current.id,
                CleaningTask.status.in_(("ready", "assigned", "pending_deadline")),
            )
        ):
            task.version += 1
            task.updated_at = now
            bump_task(session, task)
    return True


def _is_newer(incoming: NormalizedReservation, current: Reservation) -> bool:
    if (
        incoming.provider == "hospitable"
        and incoming.timestamp_provenance == "hospitable:check_in/check_out:authoritative-read"
    ):
        return incoming.observed_at > current.observed_at
    incoming_rank = _rank(incoming.source_revision)
    current_rank = _rank(current.source_revision)
    if incoming_rank is not None and current_rank is not None:
        if incoming_rank > current_rank:
            return True
        if incoming_rank < current_rank:
            return False
    if incoming.source_updated_at and current.source_updated_at:
        return incoming.source_updated_at > current.source_updated_at
    if incoming.source_updated_at and current.source_updated_at is None:
        return True
    if incoming_rank is not None and current_rank is None:
        return True
    return False


def _rank(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


def _overlap_exception(
    session: Session,
    clock: Clock,
    property_id: uuid.UUID,
    record: NormalizedReservation,
) -> None:
    if record.status != "active":
        return
    others = session.scalars(
        select(Reservation).where(
            Reservation.property_id == property_id,
            Reservation.status == "active",
            Reservation.external_id != record.external_id,
        )
    ).all()
    for other in others:
        if record.checkin_at < other.checkout_at and other.checkin_at < record.checkout_at:
            _open_exception(
                session,
                clock=clock,
                code="RESERVATION_OVERLAP",
                dedupe_key=f"overlap:{property_id}:{record.external_id}:{other.external_id}",
                sync_run_id=None,
                details={"external_ids": [record.external_id, other.external_id]},
                severity="warning",
            )


def _open_exception(
    session: Session,
    *,
    clock: Clock,
    code: str,
    dedupe_key: str,
    sync_run_id: uuid.UUID | None,
    details: dict[str, object],
    severity: str,
) -> None:
    existing = session.scalar(
        select(OperationalException).where(
            OperationalException.dedupe_key == dedupe_key,
            OperationalException.status.in_(("open", "acknowledged")),
        )
    )
    if existing is not None:
        return
    now = clock.now()
    session.add(
        OperationalException(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            code=code,
            severity=severity,
            dedupe_key=dedupe_key,
            status="open",
            task_id=None,
            assignment_id=None,
            sync_run_id=sync_run_id,
            owner_subject=None,
            details=details,
            opened_at=now,
            resolved_at=None,
            resolution_reason=None,
        )
    )
    session.flush()


def hospitable_disabled() -> None:
    HospitableReservationProvider().pull()
