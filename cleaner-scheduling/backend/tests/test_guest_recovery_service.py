from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.models.tables import CleaningTask, Reservation
from app.services import guest_recovery as service


def test_touchup_is_separate_and_idempotent(monkeypatch):
    workspace = uuid4()
    session = Mock(info={"workspace_id": workspace})
    clock = datetime.now(UTC)
    reservation = Reservation(
        id=uuid4(),
        property_id=uuid4(),
        checkin_at=clock - timedelta(hours=6),
        checkout_at=clock + timedelta(days=1),
    )
    monkeypatch.setattr(service, "_reservation", lambda *_: reservation)
    monkeypatch.setattr(service, "acquire_workspace_lock", lambda *_: None)
    rows = {}
    session.get.side_effect = lambda table, key: rows.get(key)
    session.add.side_effect = lambda task: rows.update({task.id: task})
    body = {
        "external_request_id": str(uuid4()),
        "release_at": (clock + timedelta(hours=1)).isoformat(),
        "deadline_at": (clock + timedelta(hours=3)).isoformat(),
        "scope": "Replace the bedding",
        "duration_minutes": 30,
        "guest_access_confirmed": True,
    }
    with pytest.raises(ValueError, match="confirmed entry window"):
        service.handle(session, "POST", "/v1/cleaner-returns", {**body, "guest_access_confirmed": False})
    result = service.handle(session, "POST", "/v1/cleaner-returns", body)
    task = next(iter(rows.values()))
    assert isinstance(task, CleaningTask)
    assert task.kind == "guest_touchup"
    assert task.source_reservation_id == reservation.id
    assert task.guest_scope == body["scope"]
    assert result["status"] == "needs_manager"
    assert service.handle(session, "POST", "/v1/cleaner-returns", body) == result
    session.add.assert_called_once()
    with pytest.raises(ValueError, match="different details"):
        service.handle(session, "POST", "/v1/cleaner-returns", {**body, "scope": "Different scope"})
    task.status = "completed"
    assert (
        service.handle(session, "GET", "/v1/cleaner-returns/" + body["external_request_id"], None)["status"]
        == "completed"
    )


def test_internal_timing_does_not_claim_approval(monkeypatch):
    clock = datetime.now(UTC)
    reservation = Reservation(checkin_at=clock, checkout_at=clock + timedelta(days=1))
    monkeypatch.setattr(service, "_reservation", lambda *_: reservation)
    result = service.handle(Mock(), "POST", "/v1/stay-adjustments", {"request_id": "request"})
    assert result["decision"] == "pending"
    assert result["effective_checkout_at"] == reservation.checkout_at.isoformat()
