from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.adapters.routing.baseline import DrivingBaseline
from app.api.deps import (
    get_clock,
    get_db,
    get_settings,
    require_manager,
    require_manager_idempotency_key,
    require_reader,
)
from app.api.languages import PreferredLanguage, ReadingLanguages
from app.api.routes.schedules import AvailabilityWrite, Interval, _idempotent, _save_availability
from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.domain.tasks import DEFAULT_CLEANING_MINUTES, resolve_duration
from app.models import (
    Cleaner,
    CleanerAvailability,
    CleanerWeeklyHours,
    PolicyVersion,
    Property,
    ReservationSyncRun,
    ScheduleDay,
)
from app.services.audit import write_audit
from app.services.eligibility import eligible_properties
from app.services.field_cipher import protect_text, reveal_text
from app.services.local_routing import capture_local_start, current_local_baseline
from app.services.reservation_sync import publication_blocked
from app.services.starting_address import capture_starting_routes, normalized_address
from app.services.weekly_availability import eastern_instant, effective_availability
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1", tags=["setup"])
NY = ZoneInfo("America/New_York")


@router.get("/setup")
def setup_status(
    service_date: date | None = None,
    _principal: Principal = Depends(require_reader),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    day = service_date or clock.now().astimezone(NY).date()
    properties = list(session.scalars(select(Property).where(Property.active.is_(True))))
    cleaners = list(session.scalars(select(Cleaner).where(Cleaner.active.is_(True))))
    cleaner_ids = {row.id for row in cleaners}
    eligible = {cleaner.id for cleaner in cleaners if eligible_properties(session, cleaner)}
    dated = {row.cleaner_id for row in effective_availability(session, day) if row.work_intervals and row.origin_label}
    policy = session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.is_active.is_(True),
            PolicyVersion.approved_at.is_not(None),
            PolicyVersion.effective_at <= clock.now(),
        )
        .limit(1)
    )
    baseline = (
        current_local_baseline(session, settings)
        if settings.local_free_routing
        else session.info.get("driving_baseline")
    )
    return {
        "business_date": day.isoformat(),
        "tomorrow_date": (day + timedelta(days=1)).isoformat(),
        "timezone": "America/New_York",
        "reservation_window": {
            "from_time": datetime.combine(day, time.min, tzinfo=NY).astimezone(UTC).isoformat(),
            "to_time": datetime.combine(day + timedelta(days=31), time.min, tzinfo=NY).astimezone(UTC).isoformat(),
        },
        "mode": "hospitable_read_only" if settings.hospitable_read_only else "mock",
        "last_sync": _last_sync(session) if settings.hospitable_read_only else None,
        "local_routing": {
            "configured": settings.local_free_routing,
            "houses_ready": len(baseline.property_bindings) if isinstance(baseline, DrivingBaseline) else 0,
            "cleaners_ready": sum(
                (row.origin_routes or {}).get("status") == "ready"
                and isinstance(baseline, DrivingBaseline)
                and (row.origin_routes or {}).get("baseline_id") == baseline.digest
                for row in session.scalars(
                    select(CleanerWeeklyHours).where(CleanerWeeklyHours.cleaner_id.in_(cleaner_ids))
                )
            ),
            "traffic": "none",
        },
        "properties": len(properties),
        "properties_without_explicit_duration": sum(row.duration_override_minutes is None for row in properties),
        "default_cleaning_minutes": DEFAULT_CLEANING_MINUTES,
        "cleaning_minutes_by_bedrooms": {str(bedrooms): resolve_duration(bedrooms, None)[0] for bedrooms in range(6)},
        "properties_needing_cleaning_time": 0,
        "cleaners": len(cleaners),
        "cleaners_without_eligibility": len(cleaner_ids - eligible),
        "cleaners_without_workday": len(cleaner_ids - dated),
        "tomorrow_cleaners_without_workday": len(
            cleaner_ids
            - {
                row.cleaner_id
                for row in effective_availability(session, day + timedelta(days=1))
                if row.status == "available" and row.work_intervals and row.origin_label
            }
        ),
        "cleaners_without_consent": sum(row.sms_consent_status != "opted_in" for row in cleaners),
        "policy_approved": policy is not None,
        "source_fresh": not publication_blocked(session, clock=clock, settings=settings),
        "live_automation_ready": False,
        "blockers": [
            "A verified Hospitable connection",
            "Validated production travel times",
            "SMS registration and cleaner consent",
            "Production authentication and recovery verification",
            "Complete scheduling acceptance and an approved pilot",
        ],
    }


def _last_sync(session: Session) -> dict[str, object] | None:
    row = session.scalar(
        select(ReservationSyncRun)
        .where(ReservationSyncRun.provider == "hospitable")
        .order_by(ReservationSyncRun.started_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "status": row.status,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "rows_seen": row.rows_seen,
        "rows_applied": row.rows_applied,
        "error_code": row.error_code,
    }


@router.get("/locations")
def list_locations(
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    baseline = (
        current_local_baseline(session, settings)
        if settings.local_free_routing
        else session.info.get("driving_baseline")
    )
    houses = []
    for prop in session.scalars(select(Property).where(Property.active.is_(True)).order_by(Property.name, Property.id)):
        longitude = latitude = None
        address = unit = None
        if settings.hospitable_read_only:
            if prop.location_ciphertext:
                location = json.loads(reveal_text(prop.location_ciphertext, settings.field_encryption_secret))
                point = location.get("coordinates")
                if point is not None:
                    longitude, latitude = point
                address, unit = location.get("display_address"), location.get("unit")
        else:
            longitude = float(prop.longitude) if prop.longitude is not None else None
            latitude = float(prop.latitude) if prop.latitude is not None else None
        ready = isinstance(baseline, DrivingBaseline) and prop.external_id in baseline.property_bindings
        houses.append(
            {
                "id": str(prop.id),
                "name": prop.name,
                "longitude": longitude,
                "latitude": latitude,
                "address": address,
                "unit": unit,
                "status": "mapped" if ready else "needs_review",
            }
        )
    cleaners = []
    for cleaner, week in session.execute(
        select(Cleaner, CleanerWeeklyHours)
        .outerjoin(CleanerWeeklyHours, CleanerWeeklyHours.cleaner_id == Cleaner.id)
        .where(Cleaner.active.is_(True))
        .order_by(Cleaner.display_name, Cleaner.id)
    ):
        address = (
            reveal_text(week.starting_address_ciphertext, settings.field_encryption_secret)
            if week is not None and week.starting_address_ciphertext
            else ""
        )
        routes = (week.origin_routes or {}) if week is not None else {}
        ready = (
            isinstance(baseline, DrivingBaseline)
            and routes.get("status") == "ready"
            and routes.get("baseline_id") == baseline.digest
        )
        cleaners.append(
            {
                "id": str(cleaner.id),
                "name": cleaner.display_name,
                "starting_address": address,
                "status": "ready" if ready else "needs_review" if address else "missing_address",
            }
        )
    return {"houses": houses, "cleaners": cleaners}


@router.get("/cleaners/{cleaner_id}/availability")
def get_availability(
    cleaner_id: uuid.UUID,
    service_date: date,
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    if session.get(Cleaner, cleaner_id) is None:
        raise AppError("not_found", "Cleaner was not found.", 404)
    row = session.scalar(
        select(CleanerAvailability).where(
            CleanerAvailability.cleaner_id == cleaner_id, CleanerAvailability.service_date == service_date
        )
    )
    explicit_version = 0 if row is None else row.version
    if row is None:
        row = next(
            (item for item in effective_availability(session, service_date) if item.cleaner_id == cleaner_id), None
        )
    return {
        "version": explicit_version,
        "source": None if row is None else row.origin_source,
        "status": None if row is None else row.status,
        "work_intervals": [] if row is None else row.work_intervals,
        "unavailable_intervals": [] if row is None else row.unavailable_intervals,
        "origin_label": None if row is None else row.origin_label,
    }


class WorkdayWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_date: date
    start_time: time
    end_time: time
    break_start: time | None = None
    break_end: time | None = None
    origin_label: str
    expected_version: int


@router.post("/cleaners/{cleaner_id}/workday")
def save_workday(
    cleaner_id: uuid.UUID,
    payload: WorkdayWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    converted = workday_payload(payload)

    def action() -> dict[str, object]:
        result = _save_availability(session, clock, settings, cleaner_id, converted, principal.subject)
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="cleaner.workday_saved",
            entity_type="cleaner",
            entity_id=cleaner_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Manager configured dated working hours.",
            redacted_changes={"service_date": str(payload.service_date)},
            request_id=request.state.request_id,
        )
        return result

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/workday",
        payload.model_dump(mode="json"),
        action,
        200,
    )


def workday_payload(payload: WorkdayWrite) -> AvailabilityWrite:
    start, end = (
        eastern_instant(payload.service_date, payload.start_time),
        eastern_instant(payload.service_date, payload.end_time),
    )
    if end <= start:
        raise AppError("validation_error", "Working hours must end after they start on the selected Eastern date.", 422)
    breaks = []
    if (payload.break_start is None) != (payload.break_end is None):
        raise AppError("validation_error", "Enter both break times or leave both empty.", 422)
    if payload.break_start is not None and payload.break_end is not None:
        left, right = (
            eastern_instant(payload.service_date, payload.break_start),
            eastern_instant(payload.service_date, payload.break_end),
        )
        if not start <= left < right <= end:
            raise AppError("validation_error", "The break must fit inside the working hours.", 422)
        breaks = [Interval(start_at=left, end_at=right)]
    return AvailabilityWrite(
        service_date=payload.service_date,
        status="available",
        work_intervals=[Interval(start_at=start, end_at=end)],
        unavailable_intervals=breaks,
        origin_label=payload.origin_label,
        expected_version=payload.expected_version,
    )


class WeeklyDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    break_start: time | None = None
    break_end: time | None = None
    origin_label: str | None = Field(default=None, pattern=r"^synthetic:[a-zA-Z0-9_-]+$", max_length=120)


class WeeklyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    days: list[WeeklyDay] = Field(max_length=7)
    starting_address: str | None = Field(default=None, min_length=5, max_length=300)
    reading_languages: ReadingLanguages | None = None
    preferred_language: PreferredLanguage | None = None
    expected_cleaner_version: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def language_version(self) -> WeeklyWrite:
        if (
            self.reading_languages is not None or "preferred_language" in self.model_fields_set
        ) and self.expected_cleaner_version is None:
            raise ValueError("expected_cleaner_version is required when saving reading languages.")
        return self


@router.get("/cleaners/{cleaner_id}/weekly-hours")
def get_weekly_hours(
    cleaner_id: uuid.UUID,
    _principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    cleaner = session.get(Cleaner, cleaner_id)
    if cleaner is None:
        raise AppError("not_found", "Cleaner was not found.", 404)
    row = session.get(CleanerWeeklyHours, cleaner_id)
    return {
        "version": 0 if row is None else row.version,
        "cleaner_version": cleaner.version,
        "reading_languages": cleaner.reading_languages,
        "preferred_language": cleaner.preferred_language,
        "days": [] if row is None else row.days,
        "starting_address": reveal_text(row.starting_address_ciphertext, settings.field_encryption_secret)
        if row is not None and row.starting_address_ciphertext
        else "",
        "route_status": (row.origin_routes or {}).get("status") if row is not None else None,
        "routing_mode": "local_osrm" if settings.local_free_routing else "unconfigured",
    }


@router.post("/cleaners/{cleaner_id}/weekly-hours")
def save_weekly_hours(
    cleaner_id: uuid.UUID,
    payload: WeeklyWrite,
    request: Request,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    key: str = Depends(require_manager_idempotency_key),
) -> JSONResponse:
    if len({day.weekday for day in payload.days}) != len(payload.days):
        raise AppError("validation_error", "Each weekday can appear only once.", 422)
    if payload.starting_address is None and any(day.origin_label is None for day in payload.days):
        raise AppError("validation_error", "Enter a starting address.", 422)
    for day in payload.days:
        workday_payload(
            WorkdayWrite(
                service_date=date(2026, 11, 9) + timedelta(days=day.weekday),
                expected_version=0,
                **{
                    **day.model_dump(exclude={"weekday"}),
                    "origin_label": day.origin_label or "synthetic:pending-address",
                },
            )
        )

    def action() -> dict[str, object]:
        # Same lock as publication: a weekly edit cannot race a final feasibility check.
        acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
        cleaner = session.scalar(
            select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update().execution_options(populate_existing=True)
        )
        if cleaner is None or not cleaner.active:
            raise AppError("not_found", "Cleaner was not found.", 404)
        if (
            payload.reading_languages is not None or "preferred_language" in payload.model_fields_set
        ) and cleaner.version != payload.expected_cleaner_version:
            raise AppError("stale_version", "Cleaner details changed. Load them again.", 409)
        row = session.get(CleanerWeeklyHours, cleaner_id)
        if payload.expected_version != (0 if row is None else row.version):
            raise AppError("stale_version", "Weekly hours changed. Load them again.", 409)
        days = [
            day.model_dump(mode="json", exclude_none=True) for day in sorted(payload.days, key=lambda day: day.weekday)
        ]
        address_ciphertext = None if row is None else row.starting_address_ciphertext
        routes = None if row is None else row.origin_routes
        if payload.starting_address is not None:
            address = payload.starting_address.strip()
            if len(address) < 5:
                raise AppError("validation_error", "Enter a full starting address.", 422)
            old_address = (
                reveal_text(address_ciphertext, settings.field_encryption_secret) if address_ciphertext else ""
            )
            baseline = (
                current_local_baseline(session, settings)
                if settings.local_free_routing
                else session.info.get("driving_baseline")
                if not settings.hospitable_read_only
                else None
            )
            unchanged = normalized_address(address) == normalized_address(old_address)
            if not (
                unchanged
                and routes
                and routes.get("status") == "ready"
                and isinstance(baseline, DrivingBaseline)
                and routes.get("baseline_id") == baseline.digest
            ):
                routes = (
                    capture_local_start(address, baseline)
                    if settings.local_free_routing and isinstance(baseline, DrivingBaseline)
                    else capture_starting_routes(address, baseline)
                    if isinstance(baseline, DrivingBaseline)
                    and not settings.hospitable_read_only
                    and not principal.has_role("cleaner")
                    else {"status": "routing_not_configured"}
                )
            address_ciphertext = protect_text(address, settings.field_encryption_secret)
            for day in days:
                day["origin_label"] = f"synthetic:start-{cleaner_id.hex}"

        if row is None:
            row = CleanerWeeklyHours(cleaner_id=cleaner_id, version=1, updated_at=clock.now(), days=days)
            session.add(row)
        else:
            row.version += 1
            row.updated_at = clock.now()
            row.days = days
        row.starting_address_ciphertext = address_ciphertext
        row.origin_routes = routes
        if (payload.reading_languages is not None and cleaner.reading_languages != payload.reading_languages) or (
            "preferred_language" in payload.model_fields_set
            and cleaner.preferred_language != payload.preferred_language
        ):
            if payload.reading_languages is not None:
                cleaner.reading_languages = list(payload.reading_languages)
            if "preferred_language" in payload.model_fields_set:
                cleaner.preferred_language = payload.preferred_language
            cleaner.version += 1
            cleaner.updated_at = clock.now()
            write_audit(
                session,
                clock=clock,
                actor_type="cleaner" if principal.has_role("cleaner") else "operator",
                actor_subject=principal.subject,
                action="cleaner.reading_languages_updated",
                entity_type="cleaner",
                entity_id=cleaner_id,
                correlation_id=uuid.UUID(request.state.request_id),
                reason="Updated cleaner reading languages during onboarding.",
                redacted_changes={"language_count": len(cleaner.reading_languages)},
                request_id=request.state.request_id,
            )
        for schedule_day in session.scalars(select(ScheduleDay).with_for_update()):
            schedule_day.revision += 1
            schedule_day.updated_at = clock.now()
        write_audit(
            session,
            clock=clock,
            actor_type="cleaner" if principal.has_role("cleaner") else "operator",
            actor_subject=principal.subject,
            action="cleaner.weekly_hours_saved",
            entity_type="cleaner",
            entity_id=cleaner_id,
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Cleaner submitted recurring Eastern hours."
            if principal.has_role("cleaner")
            else "Manager confirmed recurring Eastern hours.",
            redacted_changes={"weekday_count": len(days)},
            request_id=request.state.request_id,
        )
        session.flush()
        return {
            "version": row.version,
            "cleaner_version": cleaner.version,
            "reading_languages": cleaner.reading_languages,
            "preferred_language": cleaner.preferred_language,
            "days": row.days,
            "sms_sent": False,
            "route_status": (routes or {}).get("status"),
        }

    return _idempotent(
        request,
        principal,
        session,
        settings,
        clock,
        key,
        f"/v1/cleaners/{cleaner_id}/weekly-hours",
        payload.model_dump(
            mode="json",
            exclude={"reading_languages", "preferred_language", "expected_cleaner_version"} - payload.model_fields_set,
        ),
        action,
        200,
    )
