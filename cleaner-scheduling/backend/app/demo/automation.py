# ruff: noqa: B008
"""Synthetic account and supervised demonstration loop; never mounted in the normal app."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import cast

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.reservations.mock import MockFeed, MockReservationProvider, set_mock_feed
from app.adapters.routing.baseline import DrivingBaseline
from app.api.deps import get_clock, get_db, require_manager, require_manager_idempotency_key, require_reader
from app.api.routes.schedules import _idempotent
from app.clock import Clock, FrozenClock
from app.config import Settings
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import Assignment, Property, Reservation, ScheduleRun
from app.services.audit import write_audit
from app.services.evening_dispatch import record_evening_schedules
from app.services.reservation_sync import apply_normalized, create_sync_run, execute_sync
from app.services.schedules import _approved_policy, _fingerprint, preview_schedule, publish_schedule
from app.services.weekly_availability import NY, eastern_instant


class AutomationToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class DemoTurnover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_id: uuid.UUID
    checkout_date: date
    checkout_time: time
    next_checkin_time: time


def install_automation(app: FastAPI, settings: Settings) -> None:
    state: dict[str, object] = {
        "enabled": False,
        "busy": False,
        "last_run_at": None,
        "last_error": None,
        "last_outcome": "Paused",
        "published_run_id": None,
        "last_evening_dispatch": None,
    }
    last_fingerprint: str | None = None
    wake = asyncio.Event()

    def cycle(simulate_evening: bool = False) -> None:
        nonlocal last_fingerprint
        clock: Clock = app.state.clock
        with app.state.session_factory() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('demo-automation-v1'))"))
            # The simulated account replays stored synthetic bookings; no upstream calls.
            properties = {row.id: row.external_id for row in session.scalars(select(Property))}
            rows = list(session.scalars(select(Reservation).where(Reservation.provider == "mock")))
            payloads = [
                {
                    "provider": "mock",
                    "external_id": row.external_id,
                    "property_external_id": properties[row.property_id],
                    "status": row.status,
                    "checkin_at": row.checkin_at.isoformat(),
                    "checkout_at": row.checkout_at.isoformat(),
                    "source_revision": row.source_revision,
                }
                for row in rows
            ]
            set_mock_feed(MockFeed(pages=[payloads], observed_at=clock.now()))
            sync = create_sync_run(
                session, clock=clock, provider="mock", strategy="full", idempotency_key=str(uuid.uuid4())
            )
            execute_sync(session, clock=clock, sync_run_id=sync.id)
            if sync.status != "succeeded":
                raise AppError("demo_sync_failed", "The simulated reservation refresh failed.", 409)
            policy = _approved_policy(session, clock)
            if policy is None:
                raise AppError(
                    "policy_not_approved", "An owner must approve a scenario policy before demo automation.", 409
                )
            day = clock.now().astimezone(NY).date() + timedelta(days=1)
            fingerprint, schedule_day, _ = _fingerprint(session, day, policy.version_number)
            if fingerprint != last_fingerprint:
                preview = preview_schedule(
                    session, clock=clock, settings=settings, service_date=day, actor="synthetic-manager"
                )
                if preview.status == "failed":
                    raise AppError("invalid_demo_plan", "The plan needs manager review; nothing was published.", 409)
                actual = {
                    (
                        str(row.task_id),
                        str(row.cleaner_id),
                        row.planned_start,
                        row.planned_end,
                        row.travel_before_seconds,
                    )
                    for row in session.scalars(
                        select(Assignment).where(
                            Assignment.service_date == day,
                            Assignment.status.in_(("published", "acknowledged", "completed", "blocked")),
                        )
                    )
                }
                proposed = {
                    (
                        str(row["task_id"]),
                        str(row["cleaner_id"]),
                        datetime.fromisoformat(str(row["start"])),
                        datetime.fromisoformat(str(row["end"])),
                        int(str(row["travel_before_seconds"])),
                    )
                    for row in cast(list[dict[str, object]], preview.candidate_plan.get("placed", []))
                }
                # Preserve acceptance/version history when an unchanged plan is seen after a restart.
                active = session.get(ScheduleRun, schedule_day.active_run_id) if schedule_day.active_run_id else None
                if (
                    actual == proposed
                    and active is not None
                    and active.uncovered == preview.uncovered
                    and active.input_revisions.get("routing_baseline", "mock-v1")
                    == preview.input_revisions.get("routing_baseline", "mock-v1")
                ):
                    state["published_run_id"] = str(schedule_day.active_run_id)
                    session.delete(preview)
                    state["last_outcome"] = "Reservations checked; published assignments unchanged"
                else:
                    publish_schedule(
                        session,
                        clock=clock,
                        settings=settings,
                        run_id=preview.id,
                        expected_fingerprint=preview.input_fingerprint,
                        expected_day_revision=schedule_day.revision,
                        expected_version=preview.version,
                        actor="synthetic-manager",
                    )
                    state["published_run_id"] = str(preview.id)
                    state["last_outcome"] = "Next-day plan published; uncovered work remains visible as exceptions"
                last_fingerprint, _, _ = _fingerprint(session, day, policy.version_number)
            else:
                state["last_outcome"] = "Reservations checked; published assignments unchanged"
            evening_clock = clock
            if simulate_evening:
                local = clock.now().astimezone(NY)
                evening_clock = FrozenClock(
                    datetime.combine(local.date(), time.fromisoformat(settings.dispatch_local_time), NY)
                )
            dispatch = record_evening_schedules(session, clock=evening_clock, settings=settings)
            if dispatch["due"]:
                state["last_evening_dispatch"] = dispatch
            session.commit()
        state["last_run_at"] = datetime.now(UTC).isoformat()
        state["last_error"] = None

    async def worker() -> None:
        while True:
            try:
                await asyncio.wait_for(wake.wait(), timeout=30)
            except TimeoutError:
                pass
            wake.clear()
            if state["enabled"] and not state["busy"]:
                state["busy"] = True
                try:
                    await asyncio.to_thread(cycle)
                except Exception as exc:
                    logging.getLogger(__name__).error(
                        "Demo cycle paused code=%s", exc.code if isinstance(exc, AppError) else "unexpected"
                    )
                    # No raw database/provider errors or personal data in the browser.
                    state["last_error"] = (
                        exc.message if isinstance(exc, AppError) else "Demo cycle failed. Review the server logs."
                    )
                    state["enabled"] = False
                    state["last_outcome"] = "Paused after an error; manager review required"
                finally:
                    state["busy"] = False

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal wake
        wake = asyncio.Event()
        state["enabled"] = False
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            state["enabled"] = False
            # Let an in-flight transaction finish before stopping the demonstration.
            wake.set()
            while state["busy"]:
                await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.router.lifespan_context = lifespan

    @app.get("/v1/demo/account")
    def account(_principal: Principal = Depends(require_reader)) -> dict[str, object]:
        baseline = app.state.session_factory.kw.get("info", {}).get("driving_baseline")
        return {
            "routing": baseline.summary() if isinstance(baseline, DrivingBaseline) else {"mode": "synthetic"},
            "account_name": "Charlotte Demo Properties",
            "connection": "simulated",
            "provider": "mock",
            "external_account_created": False,
            "interval_seconds": 30,
            "scope": "Next-day plans only; stops on API restart",
            **state,
        }

    @app.post("/v1/demo/automation")
    async def toggle(
        payload: AutomationToggle,
        request: Request,
        principal: Principal = Depends(require_manager),
        session: Session = Depends(get_db),
        clock: Clock = Depends(get_clock),
    ) -> dict[str, object]:
        write_audit(
            session,
            clock=clock,
            actor_type="operator",
            actor_subject=principal.subject,
            action="demo.automation_changed",
            entity_type="demo_automation",
            entity_id=uuid.UUID(int=0),
            correlation_id=uuid.UUID(request.state.request_id),
            reason="Explicit local demo control.",
            redacted_changes={"enabled": payload.enabled},
            request_id=request.state.request_id,
        )
        session.commit()
        state["enabled"] = payload.enabled
        if payload.enabled:
            state["last_error"] = None
            wake.set()
        return {"enabled": state["enabled"], "busy": state["busy"]}

    @app.post("/v1/demo/evening")
    async def evening(_principal: Principal = Depends(require_manager)) -> dict[str, object]:
        if state["busy"]:
            raise AppError("demo_busy", "Wait for the current cycle to finish.", 409)
        state["busy"] = True
        try:
            await asyncio.to_thread(cycle, True)
        except Exception:
            state["enabled"] = False
            state["last_error"] = "Evening simulation failed. Review exceptions before retrying."
            raise AppError("demo_evening_failed", str(state["last_error"]), 409) from None
        finally:
            state["busy"] = False
        return {"simulated": True, "dispatch": state["last_evening_dispatch"], "external_send": False}

    @app.post("/v1/demo/turnovers")
    def turnover(
        payload: DemoTurnover,
        request: Request,
        principal: Principal = Depends(require_manager),
        session: Session = Depends(get_db),
        clock: Clock = Depends(get_clock),
        key: str = Depends(require_manager_idempotency_key),
    ) -> JSONResponse:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('demo-automation-v1'))"))

        def action() -> dict[str, object]:
            prop = session.get(Property, payload.property_id)
            if prop is None or not prop.active or prop.provider != "mock":
                raise AppError("validation_error", "Choose an active synthetic property.", 422)
            checkout = eastern_instant(payload.checkout_date, payload.checkout_time)
            arrival = eastern_instant(payload.checkout_date, payload.next_checkin_time)
            if checkout <= clock.now() or arrival <= checkout:
                raise AppError("validation_error", "Choose a future checkout and a later next arrival.", 422)
            group = uuid.uuid4()
            for suffix, checkin, out in [
                ("departure", checkout - timedelta(days=1), checkout),
                ("arrival", arrival, arrival + timedelta(days=1)),
            ]:
                raw: dict[str, object] = {
                    "provider": "mock",
                    "external_id": f"proxy-{group}-{suffix}",
                    "property_external_id": prop.external_id,
                    "status": "active",
                    "checkin_at": checkin.isoformat(),
                    "checkout_at": out.isoformat(),
                    "source_revision": "1",
                }
                for record in MockReservationProvider().normalize_event(raw):
                    apply_normalized(session, clock=clock, run=None, record=record)
            return {"status": "recorded", "group_id": str(group), "external_send": False}

        return _idempotent(
            request,
            principal,
            session,
            settings,
            clock,
            key,
            "/v1/demo/turnovers",
            payload.model_dump(mode="json"),
            action,
            201,
        )
