from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from functools import partial
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.db import catalog_session_factory, session_factory
from app.domain.errors import AppError
from app.models import Cleaner, CleanerAvailability, CleanerWeeklyHours, OutboxEvent
from app.models.workspace_connection import WorkspaceConnection
from app.services.cleaner_coordination import cleaner_portal_url, issue_portal_link
from app.services.cleaner_language import email_language, local_date
from app.services.email_addresses import normalize_email
from app.services.field_cipher import protect_text, reveal_text
from app.services.outbox_email import deliver_outbox_email, read_outbox_email, recover_stuck_outbox_emails
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID, Workspace

NY = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)
_COPY = {
    "en": (
        "Would you like to clean tomorrow — {day}?",
        "Hi {name},\n\nWould you like to work tomorrow, {day} (Eastern time)?\n\n"
        "Open your private link and choose whether you want to work:\n{url}\n\n"
        "Please respond before {cutoff} Eastern, when schedules are emailed. No response means you will not "
        "be assigned work. Your saved working hours and breaks set your capacity if you opt in. "
        "Opting in does not guarantee a job. Keep this link private.\n",
    ),
    "es": (
        "¿Quieres limpiar mañana, {day}?",
        "Hola {name},\n\n¿Quieres trabajar mañana, {day} (hora del Este)?\n\n"
        "Abre tu enlace privado y elige si quieres trabajar:\n{url}\n\n"
        "Responde antes de las {cutoff}, hora del Este, cuando se envían los horarios por correo. "
        "Si no respondes, no se te asignarán trabajos. Tus horarios y descansos guardados determinan tu "
        "capacidad si decides trabajar. Confirmar tu disponibilidad no garantiza un trabajo. "
        "Mantén este enlace privado.\n",
    ),
    "pt": (
        "Quer fazer limpezas amanhã, {day}?",
        "Olá {name},\n\nQuer trabalhar amanhã, {day} (horário do leste dos EUA)?\n\n"
        "Abra seu link privado e escolha se quer trabalhar:\n{url}\n\n"
        "Responda antes das {cutoff}, horário do leste dos EUA, quando os horários são enviados por e-mail. "
        "Se não responder, você não receberá trabalhos. Seus horários e intervalos salvos determinam sua "
        "capacidade se decidir trabalhar. Confirmar sua disponibilidade não garante um trabalho. "
        "Mantenha este link privado.\n",
    ),
}


def _snapshot(session: Session, settings: Settings, clock: Clock, cleaner_id: UUID, day: date) -> dict[str, str]:
    """Generate availability email snapshot."""
    now = clock.now().astimezone(NY)
    if day != now.date() + timedelta(days=1) or not "09:00" <= now.strftime("%H:%M") < settings.dispatch_local_time:
        raise AppError("availability_request_expired", "The availability response window has closed.", 409)
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    connection = session.get(
        WorkspaceConnection, session.info["workspace_id"], populate_existing=True, with_for_update=True
    )
    if connection is None or connection.completed_at is None:
        raise AppError("availability_request_unavailable", "Workspace setup is no longer complete.", 409)
    cleaner = session.scalar(
        select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update().execution_options(populate_existing=True)
    )
    weekly = session.scalar(
        select(CleanerWeeklyHours)
        .where(CleanerWeeklyHours.cleaner_id == cleaner_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    response = session.scalar(
        select(CleanerAvailability).where(
            CleanerAvailability.cleaner_id == cleaner_id,
            CleanerAvailability.service_date == day,
        )
    )
    if (
        cleaner is None
        or not cleaner.active
        or not cleaner.email_ciphertext
        or weekly is None
        or not weekly.starting_address_ciphertext
        or not any(int(rule["weekday"]) == day.weekday() for rule in weekly.days)
        or (response is not None and (response.origin_source == "cleaner_opt_in" or response.status != "available"))
    ):
        raise AppError("availability_request_unneeded", "This cleaner does not need an availability request.", 409)
    recipient = normalize_email(reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret))
    url = cleaner_portal_url(session, settings, clock, cleaner.id)
    if recipient is None or url is None:
        raise AppError("availability_request_unavailable", "A valid email and private cleaner link are required.", 409)
    language = email_language(cleaner) or "en"
    subject, body = _COPY[language]
    values = {
        "name": cleaner.display_name,
        "day": local_date(day),
        "url": url,
        "cutoff": settings.dispatch_local_time,
    }
    return {"recipient": recipient, "subject": subject.format(**values), "body": body.format(**values)}


def _queue(session: Session, settings: Settings, clock: Clock, day: date) -> None:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    identities = list(
        session.execute(
            select(Cleaner.id, CleanerWeeklyHours.days)
            .join(CleanerWeeklyHours, CleanerWeeklyHours.cleaner_id == Cleaner.id)
            .where(
                Cleaner.active.is_(True),
                Cleaner.email_ciphertext.is_not(None),
                CleanerWeeklyHours.starting_address_ciphertext.is_not(None),
                or_(
                    CleanerWeeklyHours.days.contains([{"weekday": day.weekday()}]),
                    CleanerWeeklyHours.days.contains([{"weekday": str(day.weekday())}]),
                ),
                ~select(OutboxEvent.id)
                .where(
                    OutboxEvent.aggregate_id == Cleaner.id,
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.payload["service_date"].as_string() == day.isoformat(),
                )
                .exists(),
                ~select(CleanerAvailability.id)
                .where(
                    CleanerAvailability.cleaner_id == Cleaner.id,
                    CleanerAvailability.service_date == day,
                    CleanerAvailability.origin_source == "cleaner_opt_in",
                )
                .exists(),
            )
            .order_by(Cleaner.id)
            .limit(100)
        )
    )
    for cleaner_id, days in identities:
        if not any(int(rule["weekday"]) == day.weekday() for rule in days):
            continue
        key = f"cleaner-availability:{cleaner_id}:{day.isoformat()}"
        if session.scalar(select(OutboxEvent.id).where(OutboxEvent.intent_key == key)) is not None:
            continue
        issue_portal_link(session, settings, clock, cleaner_id)
        try:
            snapshot = _snapshot(session, settings, clock, cleaner_id, day)
        except AppError as error:
            if error.code == "availability_request_expired":
                break
            if error.code not in {"availability_request_unneeded", "availability_request_unavailable"}:
                raise
            continue
        now = clock.now()
        event_id = uuid4()
        session.execute(
            insert(OutboxEvent)
            .values(
                id=event_id,
                created_at=now,
                updated_at=now,
                version=1,
                topic="cleaner.availability_request",
                aggregate_id=cleaner_id,
                aggregate_version=1,
                intent_key=key,
                payload={
                    "status": "queued",
                    "service_date": day.isoformat(),
                    "snapshot": protect_text(json.dumps(snapshot), settings.field_encryption_secret).hex(),
                },
                available_at=now,
                attempt_count=0,
            )
            .on_conflict_do_nothing(constraint="uq_outbox_events_intent")
        )
        logger.info("availability_email.queued event_id=%s cleaner_id=%s service_date=%s", event_id, cleaner_id, day)
    session.commit()


def _prepare(session: Session, event: OutboxEvent, settings: Settings, clock: Clock) -> dict[str, str]:
    """Prepare availability email for delivery."""
    day = date.fromisoformat(str(event.payload["service_date"]))
    current = _snapshot(session, settings, clock, event.aggregate_id, day)
    if current != read_outbox_email(event, settings):
        raise AppError("availability_request_changed", "Cleaner contact or availability changed.", 409)
    return current


def run_availability_emails(settings: Settings, clock: Clock) -> None:
    """Run availability email queue and delivery.

    M3 fix: Use ONE clock reading at the start of the workspace loop. The time window
    is checked once before queuing and delivery. If the window closes during delivery,
    treat 'expired' as a stop condition rather than an error that aborts the workspace.
    This prevents the stale vs fresh clock mismatch that can abort around 20:00.
    """
    if not (
        settings.hosted_workspace
        and settings.local_email_automation_enabled
        and settings.local_scheduling_enabled
        and settings.gmail_assignment_send_enabled
    ):
        return
    with catalog_session_factory(settings)() as catalog:
        identities = list(
            catalog.scalars(
                select(Workspace.id).where(Workspace.id != LEGACY_WORKSPACE_ID, Workspace.deleted_at.is_(None))
            )
        )
    remaining = 20
    for identity in identities:
        if remaining == 0:
            break
        try:
            now = clock.now().astimezone(NY)
            day = now.date() + timedelta(days=1)
            in_window = "09:00" <= now.strftime("%H:%M") < settings.dispatch_local_time
            factory = session_factory(settings, identity)
            with factory() as session:
                connection = session.get(WorkspaceConnection, identity)
                if connection is None or connection.completed_at is None:
                    continue
                if in_window:
                    _queue(session, settings, clock, day)
            with factory() as session:
                acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
                recover_stuck_outbox_emails(session, clock, "cleaner.availability_request")
            while remaining and in_window:
                with factory() as session:
                    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
                    event = session.scalar(
                        select(OutboxEvent)
                        .where(
                            OutboxEvent.topic == "cleaner.availability_request",
                            OutboxEvent.processed_at.is_(None),
                            OutboxEvent.available_at <= clock.now(),
                            or_(OutboxEvent.claimed_until.is_(None), OutboxEvent.claimed_until <= clock.now()),
                        )
                        .order_by(OutboxEvent.available_at, OutboxEvent.id)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                    if event is None:
                        break
                    remaining -= 1
                    deliver_outbox_email(
                        session,
                        event,
                        settings,
                        clock,
                        partial(_prepare, session, event, settings, clock),
                    )
        except AppError as error:
            if error.code == "availability_request_expired":
                logger.info("availability_email.window_closed workspace_id=%s", identity)
                break
            logger.error(
                "availability_email.app_error workspace_id=%s code=%s exception=%s",
                identity,
                error.code,
                type(error).__name__,
                exc_info=True,
            )
        except Exception as error:
            logger.error(
                "availability_email.workspace_failed workspace_id=%s exception=%s",
                identity,
                type(error).__name__,
                exc_info=True,
            )
