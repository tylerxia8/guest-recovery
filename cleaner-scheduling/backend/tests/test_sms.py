from __future__ import annotations

import logging
import socket
from datetime import UTC, datetime, timedelta

import pytest
from app.adapters.reservations.mock import MOCK_TEST_SIGNATURE
from app.adapters.sms.mock import RecordingSmsGateway, set_mock_sms_outcome
from app.clock import FrozenClock
from app.config import get_settings
from app.db import session_factory
from app.services.sms_lifecycle import drain_mock_sms, enqueue_schedule_messages, run_ack_timers
from app.workers.celery_app import celery_app
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.test_scheduling import (
    _approve,
    _available,
    _cleaner,
    _preview,
    _publish,
    _sync,
    _turnover,
)
from tests.test_scheduling import _headers as headers

UTC = UTC


def _db():
    return session_factory(get_settings(), LEGACY_WORKSPACE_ID)()


def _opt_in(client: TestClient, cleaner_id: str) -> None:
    response = client.post(
        f"/v1/cleaners/{cleaner_id}/consent",
        json={
            "status": "opted_in",
            "evidence_ref": "scenario-fixture-consent",
            "occurred_at": "2026-11-01T00:00:00Z",
            "reason": "Scenario fixture. Not a production approval.",
        },
        headers=headers("synthetic-manager", f"consent-{cleaner_id[:8]}"),
    )
    assert response.status_code == 201, response.text


def _enqueue_and_drain() -> dict[str, object]:
    from tests.conftest import TEST_CLOCK

    settings = get_settings()
    with _db() as session:
        enqueue_schedule_messages(session, clock=TEST_CLOCK, settings=settings)
        result = drain_mock_sms(session, clock=TEST_CLOCK, settings=settings)
        session.commit()
        return result


def _inbound(client: TestClient, phone: str, message_id: str, body: str) -> None:
    payload = f"from={phone}\nmessage_id={message_id}\nbody={body}\n".encode()
    response = client.post(
        "/v1/webhooks/twilio/inbound",
        content=payload,
        headers={"X-Mock-Signature": MOCK_TEST_SIGNATURE, "Content-Type": "text/plain"},
    )
    assert response.status_code == 200, response.text
    assert "<Response" in response.text


def _status(client: TestClient, provider_message_id: str, status: str) -> None:
    payload = f"message_id={provider_message_id}\nstatus={status}\n".encode()
    response = client.post(
        "/v1/webhooks/twilio/status",
        content=payload,
        headers={"X-Mock-Signature": MOCK_TEST_SIGNATURE},
    )
    assert response.status_code == 204, response.text


def _messages() -> list[tuple[object, ...]]:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT purpose, status, provider, schedule_version_number, part_number, "
                    "provider_message_id FROM sms_messages ORDER BY created_at"
                )
            ).all()
        )


def _prepare(client: TestClient, phone: str = "+15555550301") -> str:
    _approve(client)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10"), "sync-sms")
    cleaner = _cleaner(client, name="Synthetic C-A", phone=phone, properties=["P-A"], key="cleaner-sms")
    _opt_in(client, cleaner)
    from app.adapters.routing.mock import set_travel_overrides

    set_travel_overrides({("synthetic:origin-a", "P-A"): 60})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-sms")
    preview = _preview(client, "2026-11-10", "preview-sms")
    _publish(client, preview, "publish-sms")
    return cleaner


def test_mock_drain_does_not_open_a_socket(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "create_connection", fail)
    _prepare(client)
    result = _enqueue_and_drain()
    assert result["external_send"] is False
    assert result["gateway_calls"] == 1
    assert _messages()[0][2] == "mock"
    assert celery_app.conf.beat_schedule == {}


def test_at12_delivery_is_not_acceptance_and_confirm_is_versioned(client: TestClient) -> None:
    phone = "+15555550302"
    cleaner = _prepare(client, phone)
    _enqueue_and_drain()
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", key="avail-sms-2", expected_version=1)
    preview = _preview(client, "2026-11-10", "preview-sms-2")
    _publish(client, preview, "publish-sms-2")
    _enqueue_and_drain()
    delivered = [row for row in _messages() if row[0] == "assignment" and row[3] == 2]
    assert delivered
    _status(client, str(delivered[0][5]), "delivered")
    acknowledged = _rows("SELECT count(*) FROM acknowledgments")
    assert acknowledged[0][0] == 0
    _inbound(client, phone, "SM-V1", "CONFIRM 2026-11-10 V1")
    assert _rows("SELECT count(*) FROM acknowledgments")[0][0] == 0
    _inbound(client, phone, "SM-V2", "CONFIRM 2026-11-10 V2")
    _inbound(client, phone, "SM-V2B", "CONFIRM 2026-11-10 V2")
    assert _rows("SELECT count(*) FROM acknowledgments")[0][0] == 1
    assert _rows("SELECT count(*) FROM assignments WHERE status = 'acknowledged'")[0][0] >= 1


def test_at13_reminder_and_escalation_do_not_reassign(client: TestClient) -> None:
    _prepare(client, "+15555550303")
    _enqueue_and_drain()
    accepted = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as connection:
        connection.execute(
            text("UPDATE sms_messages SET accepted_at = :accepted WHERE purpose = 'assignment'"),
            {"accepted": accepted},
        )
    settings = get_settings()
    with _db() as session:
        early = run_ack_timers(session, clock=FrozenClock(accepted + timedelta(minutes=20)), settings=settings)
        session.commit()
    assert early["reminders"] == 0
    with _db() as session:
        once = run_ack_timers(session, clock=FrozenClock(accepted + timedelta(minutes=30)), settings=settings)
        drain_mock_sms(session, clock=FrozenClock(accepted + timedelta(minutes=30)), settings=settings)
        session.commit()
    with _db() as session:
        again = run_ack_timers(session, clock=FrozenClock(accepted + timedelta(minutes=30)), settings=settings)
        later = run_ack_timers(session, clock=FrozenClock(accepted + timedelta(minutes=60)), settings=settings)
        session.commit()
    assert once["reminders"] == 1
    assert again["reminders"] == 0
    assert later["escalations"] == 1
    assert later["reassigned"] is False
    assert _rows("SELECT count(*) FROM assignments WHERE status = 'published'")[0][0] == 1
    assert _rows("SELECT count(*) FROM exceptions WHERE code = 'MISSING_ACK'")[0][0] == 1


def test_at14_done_is_owned_and_deduplicated(client: TestClient) -> None:
    phone = "+15555550304"
    _prepare(client, phone)
    other = _cleaner(client, name="Synthetic C-B", phone="+15555550305", properties=["P-A"], key="cleaner-b")
    _opt_in(client, other)
    task_id = str(_rows("SELECT id FROM cleaning_tasks WHERE status = 'assigned'")[0][0])
    _inbound(client, "+15555550305", "SM-WRONG", f"DONE {task_id}")
    assert _rows("SELECT status FROM cleaning_tasks WHERE id = :id", id=task_id)[0][0] == "assigned"
    _inbound(client, phone, "SM-DONE", f"DONE {task_id}")
    _inbound(client, phone, "SM-DONE-2", f"DONE {task_id}")
    completed = _rows("SELECT status, completion_actor FROM cleaning_tasks WHERE id = :id", id=task_id)[0]
    assert completed[0] == "completed"
    assert str(completed[1]).startswith("cleaner:")
    assert _rows("SELECT count(*) FROM assignments WHERE status = 'completed'")[0][0] == 1


def test_at15_removal_notice_does_not_acknowledge_the_replacement(client: TestClient) -> None:
    phone = "+15555550311"
    first = _prepare(client, phone)
    second = _cleaner(client, name="Synthetic C-B", phone="+15555550312", properties=["P-A"], key="cleaner-b15")
    _opt_in(client, second)
    _available(client, second, "2026-11-10", "synthetic:origin-b", key="avail-b15")
    _enqueue_and_drain()
    _inbound(client, phone, "SM-OUT", "UNAVAILABLE 2026-11-10")
    assert _rows("SELECT count(*) FROM sms_messages WHERE purpose = 'cancellation'")[0][0] >= 1
    acknowledged = _rows(
        "SELECT count(*) FROM assignments WHERE cleaner_id = :id AND acknowledged_at IS NOT NULL",
        id=second,
    )
    assert acknowledged[0][0] == 0
    assert _rows("SELECT sms_consent_status FROM cleaners WHERE id = :id", id=first)[0][0] == "opted_in"


def test_at16_stale_done_does_not_complete_the_replacement(client: TestClient) -> None:
    phone = "+15555550313"
    _prepare(client, phone)
    task_id = str(_rows("SELECT id FROM cleaning_tasks WHERE status = 'assigned'")[0][0])
    _enqueue_and_drain()
    _inbound(client, phone, "SM-UNAVAIL", "UNAVAILABLE 2026-11-10")
    _inbound(client, phone, "SM-STALE-DONE", f"DONE {task_id}")
    assert _rows("SELECT count(*) FROM exceptions WHERE code = 'STALE_COMPLETION'")[0][0] == 1
    assert _rows("SELECT count(*) FROM cleaning_tasks WHERE status = 'completed'")[0][0] == 0


def test_at19_permanent_and_ambiguous_sends_do_not_retry_forever(client: TestClient) -> None:
    from tests.conftest import TEST_CLOCK

    _prepare(client, "+15555550306")
    set_mock_sms_outcome("undeliverable")
    with _db() as session:
        enqueue_schedule_messages(session, clock=TEST_CLOCK, settings=get_settings())
        first = drain_mock_sms(session, clock=TEST_CLOCK, settings=get_settings())
        second = drain_mock_sms(session, clock=TEST_CLOCK, settings=get_settings())
        session.commit()
    assert first["gateway_calls"] == 0
    assert second["recorded"] == 0
    assert _rows("SELECT status FROM sms_messages WHERE purpose = 'assignment'")[0][0] == "failed"
    assert _rows("SELECT count(*) FROM exceptions WHERE code = 'SMS_DELIVERY'")[0][0] == 1
    set_mock_sms_outcome("accepted")


def test_at26_sms_signature_and_status_do_not_regress(client: TestClient) -> None:
    _prepare(client, "+15555550307")
    _enqueue_and_drain()
    missing = client.post("/v1/webhooks/twilio/inbound", content=b"from=+15555550307\nmessage_id=SMX\nbody=HELP\n")
    assert missing.status_code == 401
    huge = client.post(
        "/v1/webhooks/twilio/inbound",
        content=b"x" * (256 * 1024 + 1),
        headers={"X-Mock-Signature": MOCK_TEST_SIGNATURE},
    )
    assert huge.status_code == 413
    _inbound(client, "+15555550307", "SM-HELP", "HELP")
    _inbound(client, "+15555550307", "SM-HELP", "HELP")
    assert _rows("SELECT count(*) FROM webhook_events WHERE dedupe_key = 'mock-sms:SM-HELP'")[0][0] == 1
    message = _rows("SELECT id, provider_message_id FROM sms_messages WHERE purpose = 'assignment'")[0]
    _status(client, str(message[1]), "delivered")
    _status(client, str(message[1]), "sent")
    assert _rows("SELECT status FROM sms_messages WHERE id = :id", id=message[0])[0][0] == "delivered"


def test_at30_flags_still_refuse_live_and_block_recording_when_enabled(client: TestClient) -> None:
    from app.services.dispatch_gate import attempt_outbound
    from app.workers.tasks import dispatch_tick

    result = dispatch_tick()
    assert result["sent"] is False
    assert result["gateway_calls"] == 0
    settings = get_settings()
    attempt = attempt_outbound(settings, policy_approved=True, gateway=RecordingSmsGateway())
    assert attempt.sent is False
    _approve(client)
    from app.config import AppEnv

    blocked = settings.model_copy(update={"production_dispatch_enabled": True})
    assert blocked.app_env == AppEnv.test
    from tests.conftest import TEST_CLOCK

    with _db() as session:
        outcome = drain_mock_sms(session, clock=TEST_CLOCK, settings=blocked)
    assert outcome["blocked"] == "flags_fail_closed"
    assert outcome["gateway_calls"] == 0


def test_at31_stop_suppresses_sends_and_bare_confirm_is_ambiguous(client: TestClient) -> None:
    phone = "+15555550308"
    cleaner = _prepare(client, phone)
    _sync(client, _turnover("R-A", "P-A", "2026-11-10") + _turnover("R-B", "P-A", "2026-11-11"), "sync-two-dates")
    _available(client, cleaner, "2026-11-11", "synthetic:origin-a", key="avail-11")
    _preview(client, "2026-11-11", "preview-11")
    _publish(client, _preview(client, "2026-11-11", "preview-11b"), "publish-11")
    _inbound(client, phone, "SM-BARE", "CONFIRM")
    assert _rows("SELECT count(*) FROM acknowledgments")[0][0] == 0
    _inbound(client, phone, "SM-STOP", "STOP")
    _inbound(client, phone, "SM-STOP-2", "STOP")
    assert _rows("SELECT sms_consent_status FROM cleaners WHERE id = :id", id=cleaner)[0][0] == "opted_out"
    assert _rows("SELECT count(*) FROM exceptions WHERE code = 'CONTACT_OPT_OUT'")[0][0] == 1
    before = _rows("SELECT count(*) FROM sms_messages WHERE status = 'accepted' AND direction = 'outbound'")[0][0]
    _enqueue_and_drain()
    after = _rows("SELECT count(*) FROM sms_messages WHERE status = 'accepted' AND direction = 'outbound'")[0][0]
    assert after == before
    _inbound(client, phone, "SM-AFTER", "CONFIRM 2026-11-10 V1")
    assert _rows("SELECT sms_consent_status FROM cleaners WHERE id = :id", id=cleaner)[0][0] == "opted_out"


def test_at33_chunks_and_rejects_malformed_commands(client: TestClient) -> None:
    phone = "+15555550309"
    _approve(client)
    stays = _turnover("A", "P-A", "2026-11-10") + _turnover("B", "P-B", "2026-11-10")
    stays += _turnover("C", "synthetic-property-001", "2026-11-10")
    _sync(client, stays, "sync-chunks")
    cleaner = _cleaner(
        client,
        name="Synthetic C-A",
        phone=phone,
        properties=["P-A", "P-B", "synthetic-property-001"],
        jobs=3,
        service=400,
        duty=600,
        key="chunk-cleaner",
    )
    _opt_in(client, cleaner)
    from app.adapters.routing.mock import set_travel_overrides

    points = ["synthetic:origin-a", "P-A", "P-B", "synthetic-property-001"]
    set_travel_overrides({(left, right): 60 for left in points for right in points if left != right})
    _available(client, cleaner, "2026-11-10", "synthetic:origin-a", end="20:00:00", key="chunk-avail")
    preview = _preview(client, "2026-11-10", "chunk-preview")
    _publish(client, preview, "chunk-publish")
    _enqueue_and_drain()
    parts = [row for row in _messages() if row[0] == "assignment"]
    assert len(parts) == 3
    assert {row[4] for row in parts} == {1, 2, 3}
    _inbound(client, phone, "SM-BAD-DONE", "DONE")
    _inbound(client, phone, "SM-EXTRA", "DONE T-A EXTRA")
    assert _rows("SELECT count(*) FROM cleaning_tasks WHERE status = 'completed'")[0][0] == 0


def test_at34_link_is_scoped_and_logs_omit_the_token(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    phone = "+15555550310"
    cleaner = _prepare(client, phone)
    issued = client.post(
        f"/v1/cleaners/{cleaner}/links",
        json={"ttl_hours": 1},
        headers=headers("synthetic-owner", "link-create", ("owner",)),
    )
    assert issued.status_code == 201, issued.text
    token = str(issued.json()["token"])
    with caplog.at_level(logging.INFO):
        response = client.get(f"/v1/cleaner-links/{token}")
    assert response.status_code == 200, response.text
    app_logs = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app."))
    assert token not in app_logs
    assert phone not in app_logs
    assert "access" not in response.text.lower()
    expired = client.post(
        f"/v1/cleaners/{cleaner}/links",
        json={"ttl_hours": 1},
        headers=headers("synthetic-owner", "link-create-2", ("owner",)),
    )
    other_token = str(expired.json()["token"])
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory.begin() as connection:
        connection.execute(
            text("UPDATE cleaner_access_links SET expires_at = '2020-01-01T00:00:00Z' WHERE token_hash <> :keep"),
            {"keep": __import__("hashlib").sha256(token.encode()).hexdigest()},
        )
    denied = client.get(f"/v1/cleaner-links/{other_token}")
    assert denied.status_code == 404
    first = _enqueue_and_drain()
    assert first["gateway_calls"] == 1
    replay = _enqueue_and_drain()
    assert replay["gateway_calls"] == 0


def _rows(query: str, **params: object) -> list[tuple[object, ...]]:
    factory = session_factory(get_settings(), LEGACY_WORKSPACE_ID)
    with factory() as connection:
        return list(connection.execute(text(query), params).all())
