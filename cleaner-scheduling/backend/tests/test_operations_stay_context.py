import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.api.routes.ingestion import operations_stay_context
from app.domain.errors import AppError
from app.domain.timeutil import dump_utc


def test_operations_stay_context_returns_effective_times_and_cleaning_completion():
    now = datetime(2026, 11, 23, 15, tzinfo=UTC)
    prop = SimpleNamespace(id=uuid4(), external_id="house", name="Fixture House")
    stay = SimpleNamespace(
        id=uuid4(),
        external_id="booking",
        version=3,
        status="active",
        approved_guest_checkin_at=now,
        checkin_at=now + timedelta(hours=1),
        approved_guest_checkout_at=None,
        checkout_at=now + timedelta(days=2),
        observed_at=now,
    )
    task = SimpleNamespace(id=uuid4(), version=2, next_reservation_id=stay.id, status="completed", completed_at=now)
    session = Mock()
    session.execute.return_value.one_or_none.return_value = (prop, stay)
    session.scalars.return_value = [task]
    settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)

    result = operations_stay_context("house", "booking", Mock(), session, settings)
    payload = json.loads(result.body)
    assert result.headers["Cache-Control"] == "no-store"
    assert payload["reservation"]["checkin_at"] == dump_utc(now)
    assert payload["reservation"]["checkout_at"] == dump_utc(stay.checkout_at)
    assert payload["departure_tasks"] == [
        {
            "id": str(task.id),
            "version": 2,
            "relationship": "before_checkin",
            "status": "completed",
            "completed_at": dump_utc(now),
        }
    ]
    assert {"house", "booking", "hospitable"} == set(session.execute.call_args.args[0].compile().params.values())


@pytest.mark.parametrize("signup,operations", [(False, True), (True, False)])
def test_operations_stay_context_requires_both_flags(signup, operations):
    session = Mock()
    settings = SimpleNamespace(workspace_signup_enabled=signup, operations_login_enabled=operations)
    with pytest.raises(AppError, match="Not found"):
        operations_stay_context("house", "booking", Mock(), session, settings)
    session.execute.assert_not_called()


def test_operations_stay_context_does_not_fall_back_after_a_missing_stay():
    session = Mock()
    session.execute.return_value.one_or_none.return_value = None
    settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)
    with pytest.raises(AppError, match="was not found in your workspace"):
        operations_stay_context("other-house", "booking", Mock(), session, settings)
    session.scalars.assert_not_called()


def test_guest_messages_require_exact_workspace_stay_before_provider_read(monkeypatch):
    from app.api.routes import ingestion

    settings = SimpleNamespace(
        workspace_signup_enabled=True,
        operations_login_enabled=True,
        operations_guest_messages_enabled=True,
        hospitable_read_only=True,
    )
    session = Mock()
    session.execute.return_value.one_or_none.return_value = None
    provider = Mock()
    monkeypatch.setattr(ingestion, "HospitableReadClient", provider)
    with pytest.raises(AppError, match="was not found in your workspace"):
        ingestion.operations_guest_messages("other-house", "other-booking", Mock(), session, settings, Mock(), Mock())
    provider.assert_not_called()
    session.get.assert_not_called()


def test_guest_messages_use_workspace_connection_and_one_provider_attempt(monkeypatch):
    from app.adapters.reservations.hospitable_read import SourceMessage
    from app.api.routes import ingestion

    now = datetime(2026, 11, 23, tzinfo=UTC)
    booking = str(uuid4())
    workspace = uuid4()
    settings = SimpleNamespace(
        workspace_signup_enabled=True,
        operations_login_enabled=True,
        operations_guest_messages_enabled=True,
        hospitable_read_only=True,
        hospitable_token_file="/unused",
        operations_guest_reply_workspace=None,
        field_encryption_secret="fixture",
    )
    stay = SimpleNamespace(
        external_id=booking,
        status="active",
        approved_guest_checkin_at=None,
        approved_guest_checkout_at=None,
        checkin_at=now - timedelta(hours=1, microseconds=180000),
        checkout_at=now + timedelta(hours=12, microseconds=713000),
    )
    session = Mock(info={"workspace_id": workspace})
    session.execute.return_value.one_or_none.return_value = (SimpleNamespace(name="Fixture home"), stay)
    session.get.return_value = SimpleNamespace(token_ciphertext=b"encrypted")
    cache = Mock()
    provider = Mock()
    provider.return_value.guest_messages.return_value = [SourceMessage("message-1", "Fixture message", now)]
    monkeypatch.setattr(ingestion, "HospitableReadClient", provider)
    reveal = Mock(return_value="fixture-token")
    monkeypatch.setattr(ingestion, "reveal_text", reveal)

    result = ingestion.operations_guest_messages(
        "house", booking, Mock(), session, settings, SimpleNamespace(now=lambda: now), cache
    )
    assert result.headers["Cache-Control"] == "no-store"
    body = json.loads(result.body)
    assert body["messages"][0]["body"] == "Fixture message"
    assert datetime.fromisoformat(body["booked_check_in"]) == stay.checkin_at
    assert datetime.fromisoformat(body["booked_check_out"]) == stay.checkout_at
    assert "fixture-token" not in result.body.decode()
    assert session.get.call_args.args[1] == workspace
    assert session.get.call_args.kwargs == {"populate_existing": True}
    cache.set.assert_called_once_with(f"turnover:guest-departure:{booking}", "1", nx=True, ex=31)
    reveal.assert_called_once_with(b"encrypted", "fixture")
    provider.assert_called_once_with("/unused", access_token="fixture-token", attempts=1, budget_seconds=5)
    provider.return_value.close.assert_called_once()


def test_repair_home_only_uses_complete_hospitable_address():
    from app.api.routes.ingestion import _repair_home
    from app.services.field_cipher import protect_text

    settings = SimpleNamespace(field_encryption_secret="fixture-only")
    prop = SimpleNamespace(name="Fixture Home", timezone="America/New_York")
    for source, address in [
        ("local", "20 Synthetic Street, Charlotte, NC 28000, US"),
        ("hospitable", "20 Synthetic Street, Charlotte"),
    ]:
        prop.location_ciphertext = protect_text(
            json.dumps({"source": source, "display_address": address}), settings.field_encryption_secret
        )
        assert _repair_home(prop, settings) is None
    prop.location_ciphertext = protect_text(
        json.dumps(
            {
                "source": "hospitable",
                "display_address": "20 Synthetic Street, Charlotte, NC 28000, US",
                "unit": "2A",
            }
        ),
        settings.field_encryption_secret,
    )
    assert _repair_home(prop, settings) == {
        "name": "Fixture Home",
        "timezone": "America/New_York",
        "address_line1": "20 Synthetic Street",
        "city": "Charlotte",
        "region": "NC",
        "postal_code": "28000",
        "country_code": "US",
        "unit_label": "2A",
    }
