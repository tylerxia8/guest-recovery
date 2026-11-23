from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

from app.models.tables import Assignment, CleaningTask, Reservation
from app.services import guest_stay_policy as policy

NOW = datetime(2026, 11, 20, 22, tzinfo=UTC)
CHECKOUT = datetime(2026, 11, 21, 15, tzinfo=UTC)


def booking(start, end):
    return Reservation(id=uuid4(), property_id=uuid4(), checkin_at=start, checkout_at=end, version=1)


def test_checkout_rules_preserve_schedule_and_confirmed_release(monkeypatch):
    reservation = booking(NOW - timedelta(days=2), CHECKOUT)
    task = CleaningTask(
        status="ready", release_at=CHECKOUT, deadline_at=CHECKOUT + timedelta(hours=7), duration_minutes=90
    )
    monkeypatch.setattr(policy, "_fresh", lambda *_: True)
    monkeypatch.setattr(policy, "_departure_task", lambda *_: task)
    recompute = Mock()
    monkeypatch.setattr(policy, "recompute_property_tasks", recompute)
    monkeypatch.setattr(policy, "write_audit", Mock())
    session = Mock(info={})
    session.scalars.side_effect = [[], []]
    session.scalar.side_effect = [None, None]
    target = CHECKOUT + timedelta(hours=1)
    assert policy.assess(session, reservation, "one", "late_checkout", target, NOW)["decision"] == "approved"
    assert reservation.approved_guest_checkout_at == target
    recompute.assert_called_once()

    reservation.approved_guest_checkout_at = None
    session.scalars.side_effect = [[], []]
    session.scalar.side_effect = [None, uuid4()]
    assert policy.assess(session, reservation, "released", "late_checkout", target, NOW)["decision"] == "pending"

    target = CHECKOUT + timedelta(hours=2)
    assignment = Assignment(
        status="published",
        planned_start=target + timedelta(minutes=15),
        planned_end=target + timedelta(minutes=105),
        duration_minutes=90,
    )
    session.scalars.side_effect = [[], [assignment]]
    session.scalar.side_effect = [None, uuid4()]
    assert policy.assess(session, reservation, "long", "late_checkout", target, NOW)["decision"] == "approved"
    assert reservation.approved_guest_checkout_at == target

    reservation.approved_guest_checkout_at = None
    neighbor = booking(target - timedelta(minutes=10), target + timedelta(days=1))
    session.scalars.side_effect = [[neighbor], [assignment]]
    session.scalar.side_effect = [None, uuid4()]
    assert policy.assess(session, reservation, "overlap", "late_checkout", target, NOW)["reason"] == (
        "another_guest_stay_conflicts"
    )


def test_early_checkin_requires_recorded_completion(monkeypatch):
    booked = CHECKOUT + timedelta(hours=6)
    now = CHECKOUT + timedelta(hours=4)
    incoming = booking(booked, booked + timedelta(days=2))
    departed = booking(CHECKOUT - timedelta(days=2), CHECKOUT)
    task = CleaningTask(status="assigned")
    session = Mock(info={})
    session.scalars.return_value = [departed]
    monkeypatch.setattr(policy, "_fresh", lambda *_: True)
    monkeypatch.setattr(policy, "_departure_task", lambda *_: task)
    monkeypatch.setattr(policy, "recompute_property_tasks", Mock())
    monkeypatch.setattr(policy, "write_audit", Mock())
    target = CHECKOUT + timedelta(hours=3)
    assert policy.assess(session, incoming, "early", "early_checkin", target, now)["decision"] == "pending"
    task.status = "completed"
    task.completed_at = target + timedelta(minutes=15)
    result = policy.assess(session, incoming, "early", "early_checkin", target, now)
    assert result["decision"] == "approved"
    assert result["target_at"] == task.completed_at.isoformat()
    assert incoming.approved_guest_checkin_at == task.completed_at
