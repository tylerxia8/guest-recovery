from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.clock import FrozenClock
from app.config import get_settings
from app.domain.tasks import effective_deadline, turnover_deadline
from app.models import SmsMessage
from app.services.evening_dispatch import record_evening_schedules
from app.services.field_cipher import reveal_text
from sqlalchemy import select
from tests.completed_fixture import db
from tests.test_scheduling import _approve, _available, _preview, _publish, _stay, _sync, _tasks
from tests.test_setup import cleaner, headers
from tests.test_sms import _opt_in


@pytest.mark.parametrize("release", ["2026-03-07T10:00:00-05:00", "2026-10-31T10:00:00-04:00"])
def test_turnover_limit_is_48_elapsed_hours_across_dst(release):
    checkout = datetime.fromisoformat(release)
    cap = turnover_deadline(checkout)
    assert cap - checkout.astimezone(UTC) == timedelta(hours=48)
    assert turnover_deadline(checkout, checkout + timedelta(hours=4)) == checkout + timedelta(hours=4)
    assert effective_deadline(checkout, checkout + timedelta(days=10)) == cap


def test_carryover_is_visible_and_assigned_on_service_day_with_evening_schedule(client):
    _approve(client)
    _sync(client, [_stay("carryover", "P-A", "2026-11-09T16:00:00-05:00", "2026-11-10T10:00:00-05:00")], "carry-sync")
    task = _tasks(client)[0]
    assert task["deadline_at"] == "2026-11-12T15:00:00+00:00"
    cleaner_id = cleaner(client)
    _opt_in(client, cleaner_id)
    _available(client, cleaner_id, "2026-11-11", "synthetic:carry-origin", key="carry-day")
    before = client.get("/v1/dashboard?service_date=2026-11-11", headers=headers()).json()
    assert [t["id"] for t in before["today"]] == [task["id"]]
    assert before["unassigned_count"] == 1
    preview = _preview(client, "2026-11-11", "carry-preview")
    assert len(preview["candidate_plan"]["placed"]) == 1
    _publish(client, preview, "carry-publish")
    after = client.get("/v1/dashboard?service_date=2026-11-11", headers=headers()).json()
    row = after["today"][0]
    assert row["cleaner_name"] == "Synthetic setup cleaner"
    assert row["scheduled_date"] == "2026-11-11" and row["cleaned"] is False
    assert after["unassigned_count"] == 0
    settings = get_settings()
    with db() as session:
        early = FrozenClock(datetime(2026, 11, 10, 19, 59, tzinfo=ZoneInfo("America/New_York")))
        assert record_evening_schedules(session, clock=early, settings=settings)["due"] is False
        evening = FrozenClock(datetime(2026, 11, 10, 20, 1, tzinfo=ZoneInfo("America/New_York")))
        first = record_evening_schedules(session, clock=evening, settings=settings)
        assert first["recorded"] == 1 and first["external_send"] is False
        assert record_evening_schedules(session, clock=evening, settings=settings)["recorded"] == 0
        message = session.scalar(select(SmsMessage).where(SmsMessage.purpose == "assignment"))
        body = reveal_text(message.body_encrypted, settings.auth_mock_secret)
        assert "ready by Nov 12 10:00 Eastern" in body and "Reply DONE" in body and "Reply CONFIRM" in body
        session.commit()
    # A later daily run must not duplicate or move another day's live assignment.
    other = _preview(client, "2026-11-12", "carry-other")
    assert other["candidate_plan"]["placed"] == []


def test_overdue_unassigned_houses_remain_visible_beyond_selected_day(client):
    _approve(client)
    _sync(client, [_stay("late", "P-A", "2026-11-01T16:00:00-05:00", "2026-11-02T10:00:00-05:00")], "late-sync")
    body = client.get("/v1/dashboard?service_date=2026-11-10", headers=headers()).json()
    assert len(body["overdue"]) == 1
    assert body["overdue"][0]["needs_assignment"] is True
    assert body["overdue"][0]["overdue"] is True
