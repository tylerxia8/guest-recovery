"""Seed a fresh, explicitly named demo database through the real application API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.reservations.mock import MockFeed, set_mock_feed
from app.clock import FrozenClock
from app.config import Settings, get_settings
from app.demo.server import DEMO_NOW, build_demo_app, guard, token
from app.models import CleaningTask, Property
from app.seed import seed_operators, seed_properties

POLICY = {
    "schema_version": 1,
    "availability_request_local_time": "17:00",
    "availability_cutoff_local_time": "17:30",
    "dispatch_local_time": "20:00",
    "ack_reminder_minutes": 30,
    "ack_escalation_minutes": 60,
    "readiness_buffer_minutes": 30,
    "auto_reassign_missing_ack": False,
    "pre_arrival_horizon_hours": 48,
    "duration_minutes_by_bedrooms": {"0": 60, "1": 90, "2": 120, "3": 150, "4": 180},
    "solver_time_limit_seconds": 30,
    "reservation_reconcile_minutes": 15,
    "routing_cache_ttl_minutes": 60,
    "integration_stale_threshold_minutes": 30,
    "mandatory_return_to_base": False,
}


def seed(settings: Settings) -> None:
    guard(settings)
    app = build_demo_app(settings)
    with app.state.session_factory() as session:
        if session.scalar(select(CleaningTask.id).limit(1)) is not None:
            raise RuntimeError("Demo already seeded. Use the documented isolated database reset.")
        seed_operators(session, FrozenClock(DEMO_NOW))
        seed_properties(session, FrozenClock(DEMO_NOW))
        properties = list(session.scalars(select(Property).order_by(Property.external_id)).all())
        chosen = properties[:6]
        names = ["Willow Cottage", "Juniper House", "Maple Loft", "Cedar Retreat", "Magnolia Studio", "Oak Terrace"]
        for prop, name in zip(chosen, names, strict=True):
            prop.name = f"{name} (synthetic)"
            prop.duration_override_minutes = 90
        session.commit()
        ids = [str(prop.id) for prop in chosen]
        external = [prop.external_id for prop in chosen]
    with TestClient(app, client=("127.0.0.1", 50000)) as client:

        def post(
            path: str, body: dict[str, object], *, owner: bool = False, match: int | None = None
        ) -> dict[str, Any]:
            headers = {
                "Authorization": f"Bearer {token(settings, 'owner' if owner else 'manager')}",
                "Idempotency-Key": str(uuid.uuid4()),
            }
            if match is not None:
                headers["If-Match"] = f'"{match}"'
            response = client.post(f"/v1{path}", json=body, headers=headers)
            if not response.is_success:
                raise RuntimeError(f"Demo setup failed at {path}: {response.status_code} {response.text}")
            return dict(response.json())

        policy = post("/policy-versions", {"settings": POLICY, "reason": "Synthetic demo policy only."}, owner=True)
        post(
            f"/policy-versions/{policy['id']}/approve",
            {"approval_note": "Synthetic demo only.", "effective_at": "2026-11-01T00:00:00Z"},
            owner=True,
            match=1,
        )
        stays: list[dict[str, object]] = []
        for index, prop in enumerate(external):
            day = "2026-11-10" if index < 4 else "2026-11-11"
            previous = "2026-11-09" if index < 4 else "2026-11-10"
            next_day = "2026-11-11" if index < 4 else "2026-11-12"
            for suffix, checkin, checkout in [
                ("departure", f"{previous}T16:00:00-05:00", f"{day}T10:00:00-05:00"),
                ("arrival", f"{day}T16:00:00-05:00", f"{next_day}T10:00:00-05:00"),
            ]:
                stays.append(
                    {
                        "provider": "mock",
                        "external_id": f"demo-{index}-{suffix}",
                        "property_external_id": prop,
                        "status": "active",
                        "checkin_at": checkin,
                        "checkout_at": checkout,
                        "source_revision": "1",
                    }
                )
        set_mock_feed(MockFeed(pages=[stays], observed_at=DEMO_NOW))
        post("/reservation-syncs", {"provider": "mock", "mode": "full"})
        for index, name in enumerate(["Alex Demo", "Morgan Demo", "Jordan Demo"]):
            cleaner = post(
                "/cleaners",
                {
                    "display_name": name,
                    "phone": f"+1555555060{index}",
                    "max_jobs": 2,
                    "max_service_minutes": 240,
                    "max_duty_minutes": 480,
                    "eligibility_property_ids": ids,
                },
            )
            cleaner_id = cleaner["id"]
            post(
                f"/cleaners/{cleaner_id}/consent",
                {
                    "status": "opted_in",
                    "evidence_ref": "demo-synthetic-only",
                    "occurred_at": "2026-11-01T00:00:00Z",
                    "reason": "Synthetic demonstration only.",
                },
            )
            for day in ["2026-11-10", "2026-11-11"]:
                post(
                    f"/cleaners/{cleaner_id}/availability",
                    {
                        "service_date": day,
                        "status": "available",
                        "work_intervals": [{"start_at": f"{day}T09:00:00-05:00", "end_at": f"{day}T17:00:00-05:00"}],
                        "origin_label": f"synthetic:demo-origin-{index}",
                    },
                )
        preview = post("/schedules/previews", {"service_date": "2026-11-10"})
        post(
            f"/schedules/runs/{preview['id']}/publish",
            {"expected_fingerprint": preview["input_fingerprint"], "expected_day_revision": preview["day_revision"]},
            match=int(preview["version"]),
        )
        post("/demo/record-sms", {})
        rows = client.get(
            "/v1/demo/assignments", headers={"Authorization": f"Bearer {token(settings, 'manager')}"}
        ).json()["items"]
        # One completed job and one explicit acceptance give the tour useful starting facts.
        for command, row in zip(["DONE", "CONFIRM"], rows, strict=False):
            post("/demo/reply", {"assignment_id": row["id"], "command": command, "event_id": str(uuid.uuid4())})
    print("Demo seeded: November 10, 2026 at 1 PM Eastern. Recorded mock SMS only; no payment setup required.")


if __name__ == "__main__":
    seed(get_settings())
