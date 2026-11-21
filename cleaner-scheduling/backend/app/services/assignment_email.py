from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.tasks import effective_deadline
from app.models import (
    Assignment,
    Cleaner,
    CleanerScheduleVersion,
    CleaningTask,
    OperationalException,
    OutboxEvent,
    Property,
    ScheduleDay,
    ScheduleRun,
)
from app.services.cleaner_coordination import cleaner_portal_url
from app.services.cleaner_language import email_language, local_date, localized_email
from app.services.field_cipher import reveal_text
from app.services.idempotency import request_digest
from app.services.local_routing import current_local_baseline
from app.services.reservation_sync import publication_blocked
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id


def assignment_preview(
    session: Session, settings: Settings, clock: Clock, cleaner_id: uuid.UUID, day: date
) -> dict[str, object]:
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    if publication_blocked(session, clock=clock, settings=settings):
        raise AppError("stale_source", "Refresh reservation imports before sending assignments.", 409)
    cleaner = session.scalar(select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update())
    if cleaner is None or not cleaner.active or not cleaner.email_ciphertext:
        raise AppError("cleaner_email_missing", "Save an email for this active cleaner first.", 409)
    versions = list(
        session.scalars(
            select(CleanerScheduleVersion)
            .where(
                CleanerScheduleVersion.cleaner_id == cleaner_id,
                CleanerScheduleVersion.service_date == day,
                CleanerScheduleVersion.status == "current",
            )
            .with_for_update()
        )
    )
    if len(versions) != 1:
        raise AppError("schedule_unavailable", "A unique current published schedule is required.", 409)
    version = versions[0]
    run = session.scalar(select(ScheduleRun).where(ScheduleRun.id == version.schedule_run_id).with_for_update())
    if run is None or run.status != "published" or run.service_date != day:
        raise AppError("schedule_unavailable", "The schedule has not been published.", 409)
    assignments = list(
        session.scalars(
            select(Assignment)
            .where(
                Assignment.cleaner_id == cleaner_id,
                Assignment.service_date == day,
                Assignment.status.not_in(("superseded", "completed", "canceled")),
            )
            .order_by(Assignment.planned_start, Assignment.id)
            .with_for_update()
        )
    )
    if not assignments:
        raise AppError("schedule_empty", "There are no upcoming published assignments to email.", 409)
    lines: list[str] = []
    localized_jobs: list[dict[str, str]] = []
    bindings: list[dict[str, object]] = []
    assignment_content: dict[str, object] = {}
    upcoming: list[Assignment] = []
    ny = ZoneInfo("America/New_York")
    for assignment in assignments:
        task = session.scalar(select(CleaningTask).where(CleaningTask.id == assignment.task_id).with_for_update())
        if (
            assignment.status not in {"published", "acknowledged"}
            or assignment.cleaner_id != cleaner_id
            or assignment.service_date != day
            or assignment.planned_end <= clock.now()
            or task is None
            or task.status != "assigned"
            or task.duration_minutes is None
            or assignment.duration_minutes != task.duration_minutes
        ):
            raise AppError("assignment_changed", "An assignment is no longer ready to email. Review the schedule.", 409)
        if (
            session.scalar(
                select(OperationalException.id)
                .where(
                    OperationalException.status.in_(("open", "acknowledged")),
                    (OperationalException.task_id == task.id) | (OperationalException.assignment_id == assignment.id),
                )
                .limit(1)
            )
            is not None
        ):
            raise AppError("assignment_blocked", "Resolve the assignment exceptions before emailing it.", 409)
        if (
            session.scalar(
                select(Assignment.id)
                .where(
                    Assignment.task_id == task.id,
                    Assignment.id != assignment.id,
                    Assignment.status.in_(("published", "acknowledged", "blocked")),
                )
                .limit(1)
            )
            is not None
        ):
            raise AppError(
                "assignment_conflict", "The task has competing active assignments. Review the schedule.", 409
            )
        assignment_run = session.get(ScheduleRun, assignment.schedule_run_id)
        if assignment_run is None or assignment_run.status != "published":
            raise AppError("schedule_unavailable", "An assignment does not belong to a published schedule.", 409)
        deadline = effective_deadline(task.release_at, task.deadline_at, task.manager_target_at)
        if (
            assignment.planned_start < task.release_at
            or assignment.planned_end > deadline
            or assignment.planned_end <= assignment.planned_start
            or assignment.planned_end - assignment.planned_start != timedelta(minutes=assignment.duration_minutes)
        ):
            raise AppError("assignment_infeasible", "The assignment timing must be reviewed before emailing.", 409)
        prop = session.scalar(select(Property).where(Property.id == task.property_id).with_for_update())
        if prop is None or not prop.active or not prop.location_ciphertext:
            raise AppError("missing_destination", "A scheduled house needs a saved address before messaging.", 409)
        location = json.loads(reveal_text(prop.location_ciphertext, settings.field_encryption_secret))
        if (
            not isinstance(location, dict)
            or not isinstance(location.get("display_address"), str)
            or not location["display_address"].strip()
        ):
            raise AppError("missing_destination", "A scheduled house needs a saved address before messaging.", 409)
        address = location["display_address"].strip()
        unit = location.get("unit")
        if unit:
            address += f", unit {unit}"
        if assignment.planned_start <= clock.now():
            continue
        upcoming.append(assignment)
        start = assignment.planned_start.astimezone(ny).strftime("%I:%M %p")
        end = assignment.planned_end.astimezone(ny).strftime("%I:%M %p")
        due = deadline.astimezone(ny).strftime("%b %d, %I:%M %p")
        lines.append(
            f"{len(lines) + 1}. {prop.name}\nAddress: {address}\n"
            f"Cleaning window: {start}–{end} Eastern\n"
            f"Estimated cleaning time: {assignment.duration_minutes} minutes\nReady by: {due} Eastern"
        )
        localized_jobs.append(
            {
                "number": str(len(lines)),
                "house": prop.name,
                "address": address,
                "start": assignment.planned_start.astimezone(ny).strftime("%H:%M"),
                "end": assignment.planned_end.astimezone(ny).strftime("%H:%M"),
                "minutes": str(assignment.duration_minutes),
                "due": local_date(deadline),
            }
        )
        assignment_content[str(assignment.id)] = {
            key: value for key, value in localized_jobs[-1].items() if key != "number"
        }
        bindings.append(
            {
                "assignment_id": str(assignment.id),
                "assignment_version": assignment.version,
                "task_id": str(task.id),
                "task_version": task.version,
                "property_version": prop.version,
            }
        )
    if not upcoming:
        raise AppError("schedule_empty", "There are no upcoming published assignments to email.", 409)
    recipient = reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret)
    subject = f"Your cleaning schedule — {day.isoformat()} — V{version.version_number}"
    content = (
        f"Hi {cleaner.display_name},\n\n"
        f"Your published cleaning schedule for {day.strftime('%A, %B %d, %Y')} (Eastern time):\n\n"
        + "\n\n".join(lines)
        + "\n\nContact your property manager if you cannot cover an assignment.\n"
    )
    portal_url = cleaner_portal_url(session, settings, clock, cleaner.id)
    if portal_url is not None:
        local = settings.cleaner_portal_base_url.startswith(("http://localhost", "http://127.0.0.1"))
        content += "\nOpen your schedule" + (" (on this computer)" if local else "") + f": {portal_url}\n"
        content += "Use the portal to accept, mark cleaning done, or release a house you cannot clean.\n"
    semantic_body = content
    subject, content = localized_email(
        cleaner,
        "assignment",
        subject,
        content,
        {
            "day": local_date(day),
            "version": str(version.version_number),
            "url": portal_url or "",
            "local": "yes"
            if settings.cleaner_portal_base_url.startswith(("http://localhost", "http://127.0.0.1"))
            else "",
        },
        localized_jobs,
    )
    preview: dict[str, object] = {
        "semantic_body": semantic_body,
        "language": email_language(cleaner),
        "cleaner_id": str(cleaner.id),
        "cleaner_version": cleaner.version,
        "recipient": recipient,
        "subject": subject,
        "body": content,
        "portal_url": portal_url,
        "assignment_ids": [str(row.id) for row in upcoming],
        "task_ids": [str(row.task_id) for row in upcoming],
        "assignment_content": assignment_content,
    }
    return {
        **preview,
        "fingerprint": request_digest(
            {
                **preview,
                "schedule_version_id": str(version.id),
                "schedule_version": version.version_number,
                "schedule_run_version": run.version,
                "assignments": bindings,
            }
        ),
    }


def reservation_change_preview(
    session: Session, settings: Settings, clock: Clock, event_id: uuid.UUID
) -> dict[str, object]:
    if publication_blocked(session, clock=clock, settings=settings):
        raise AppError("stale_source", "Refresh reservation imports before sending changes.", 409)
    event = session.get(OutboxEvent, event_id)
    if event is None or event.topic != "cleaning.reservation_changed":
        raise AppError("change_unavailable", "The reservation change is unavailable.", 409)
    assignment = session.get(Assignment, event.aggregate_id)
    cleaner = None if assignment is None else session.get(Cleaner, assignment.cleaner_id)
    task = None if assignment is None else session.get(CleaningTask, assignment.task_id)
    prop = None if task is None else session.get(Property, task.property_id)
    if assignment is None or task is None or prop is None:
        raise AppError("change_unavailable", "The changed assignment is unavailable.", 409)
    if cleaner is None or not cleaner.active or not cleaner.email_ciphertext:
        raise AppError("cleaner_email_missing", "Save an email for this active cleaner first.", 409)
    day = assignment.service_date
    change = event.payload.get("change")
    if change == "removed":
        explanation = (
            f"The booking for {prop.name} changed. Your previous cleaning assignment for "
            f"{day.isoformat()} has been removed. Do not follow its previous cleaning time."
        )
    elif change == "review":
        explanation = (
            f"The booking for {prop.name} changed and affects a locked or already-started cleaning. "
            "Contact your property manager to confirm what to do."
        )
    else:
        raise RuntimeError("The reservation change has no supported action.")
    schedule_body = None
    semantic_schedule_body = None
    assignment_content: object = None
    notified_tasks = {str(task.id)}
    schedule_day = session.scalar(select(ScheduleDay).where(ScheduleDay.service_date == day))
    marker = session.scalar(
        select(OutboxEvent).where(OutboxEvent.intent_key == f"automatic-cleaner-plan:{day.isoformat()}")
    )
    if (
        schedule_day is not None
        and marker is not None
        and marker.payload.get("day_revision") == schedule_day.revision
        and marker.payload.get("schedule_run_id") == str(schedule_day.active_run_id)
        and current_local_baseline(session, settings) is not None
    ):
        try:
            schedule = assignment_preview(session, settings, clock, cleaner.id, day)
            schedule_body = str(schedule["body"])
            semantic_schedule_body = str(schedule["semantic_body"])
            assignment_content = schedule["assignment_content"]
            schedule_task_ids = schedule["task_ids"]
            if not isinstance(schedule_task_ids, list):
                raise RuntimeError("The schedule preview has no task manifest.")
            notified_tasks.update(str(item) for item in schedule_task_ids)
        except AppError as error:
            if error.code == "stale_source":
                raise
    portal_url = cleaner_portal_url(session, settings, clock, cleaner.id)
    content = f"Hi {cleaner.display_name},\n\n{explanation}\n\n"
    if schedule_body is not None:
        content += "Your updated published schedule follows. It replaces the earlier schedule email.\n\n" + str(
            semantic_schedule_body
        )
    else:
        content += (
            "No replacement cleaning time is confirmed in this notice. Check your current schedule for other jobs.\n"
        )
        if portal_url is not None:
            content += f"\nOpen your schedule: {portal_url}\n"
    subject, content = localized_email(
        cleaner,
        "change",
        f"Cleaning schedule changed — {day.isoformat()}",
        content,
        {
            "day": local_date(day),
            "house": prop.name,
            "change": str(change),
            "url": portal_url or "",
            "schedule_body": schedule_body or "",
        },
    )
    preview: dict[str, object] = {
        "semantic_body": semantic_schedule_body,
        "language": email_language(cleaner),
        "cleaner_id": str(cleaner.id),
        "cleaner_version": cleaner.version,
        "recipient": reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret),
        "subject": subject,
        "body": content,
        "portal_url": portal_url,
        "notification": "reservation_change",
        "task_ids": sorted(notified_tasks),
        "event_id": str(event.id),
        "schedule_body": schedule_body,
        "assignment_content": assignment_content,
    }
    return {
        **preview,
        "fingerprint": request_digest(
            {
                **preview,
                "event_version": event.version,
                "assignment_version": assignment.version,
                "task_version": task.version,
            }
        ),
    }
