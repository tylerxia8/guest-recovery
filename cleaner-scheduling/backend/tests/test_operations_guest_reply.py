from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.adapters.reservations.hospitable_read import SourceMessage
from app.api.routes import ingestion
from app.domain.errors import AppError
from app.models import IdempotencyRecord, Property, Reservation
from app.models.workspace_connection import WorkspaceConnection
from app.services.field_cipher import protect_text
from app.workspaces import LEGACY_WORKSPACE_ID
from sqlalchemy import select


@pytest.mark.parametrize("first_status", ["accepted", "unknown", "rejected"])
def test_private_reply_claim_survives_retries_and_never_stores_guest_text(
    session, settings, clock, monkeypatch, first_status
):
    now = clock.now()
    home, booking = uuid4(), uuid4()
    prop = Property(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        provider="hospitable",
        external_id=str(home),
        name="Fixture Property",
        bedrooms=2,
        location_ciphertext=b"fictional",
        geocode_status="synthetic",
        active=True,
    )
    session.add(prop)
    session.flush()
    session.add(
        Reservation(
            id=uuid4(),
            created_at=now,
            updated_at=now,
            property_id=prop.id,
            provider="hospitable",
            external_id=str(booking),
            status="active",
            checkin_at=now - timedelta(hours=3),
            checkout_at=now + timedelta(days=1),
            observed_at=now,
            timestamp_provenance="source",
            payload_digest="a" * 64,
        )
    )
    shared = settings.model_copy(
        update={
            "workspace_signup_enabled": True,
            "workspace_registration_enabled": False,
            "operations_login_enabled": True,
            "operations_guest_messages_enabled": True,
            "operations_guest_reply_workspace": LEGACY_WORKSPACE_ID,
            "hospitable_read_only": True,
            "field_encryption_key_file": "/tmp/unused",
        }
    )
    monkeypatch.setattr(type(shared), "field_encryption_secret", property(lambda _: "fixture-only"))
    session.add(
        WorkspaceConnection(
            workspace_id=LEGACY_WORKSPACE_ID,
            connected_at=now,
            token_ciphertext=protect_text("opaque-fixture-token", "fixture-only"),
        )
    )
    session.commit()
    reads = Mock()
    messages = [SourceMessage("fixture-message", "Fixture body", now)]
    reads.return_value.message_context.return_value = (messages, "b" * 64)
    monkeypatch.setattr(ingestion, "HospitableReadClient", reads)
    cache = Mock()
    send = Mock(return_value=first_status)
    monkeypatch.setattr(ingestion, "submit_reply", send)
    monkeypatch.setattr(ingestion, "enforce_rate_limit", Mock())
    payload = ingestion.OperationsGuestReply(
        property_external_id=home,
        reservation_external_id=booking,
        message_id="fixture-message",
        thread_fingerprint="b" * 64,
        body="Fixture answer for guest",
        approved=True,
    )

    def post(fields=payload, config=shared):
        return ingestion.operations_guest_reply(fields, None, session, config, clock, cache)

    with pytest.raises(AppError) as outside:
        post(config=shared.model_copy(update={"operations_guest_reply_workspace": uuid4()}))
    assert outside.value.status_code == 404
    with pytest.raises(AppError) as stale:
        post(payload.model_copy(update={"thread_fingerprint": "c" * 64}))
    assert stale.value.status_code == 409
    assert session.scalar(select(IdempotencyRecord)) is None
    send.assert_not_called()

    assert post().body == ('{"status":"' + first_status + '"}').encode()
    assert post().body == ('{"status":"' + first_status + '"}').encode()
    send.assert_called_once_with("opaque-fixture-token", booking, "Fixture answer for guest")
    with pytest.raises(AppError) as conflict:
        post(payload.model_copy(update={"body": "Edited answer from another manager"}))
    assert conflict.value.status_code == 409
    row = session.scalar(select(IdempotencyRecord))
    assert row is not None
    assert row.response_body == {"status": first_status}
    assert "Fixture" not in str(row.response_body)
    assert "fixture-message" not in row.key


@pytest.mark.parametrize(
    ("response_code", "provider_payload", "expected"),
    [
        (202, {"data": {"sent_reference_id": "fixture-reference"}}, "accepted"),
        (202, {"data": {}}, "unknown"),
        (429, {}, "rejected"),
        (503, {}, "unknown"),
        (307, {}, "unknown"),
    ],
)
def test_hospitable_provider_contract_never_follows_redirects_or_retries(
    monkeypatch, response_code, provider_payload, expected
):
    import json

    import httpx
    from app.adapters.reservations.hospitable_reply import submit_reply

    reservation = uuid4()
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url == f"https://public.api.hospitable.com/v2/reservations/{reservation}/messages"
        assert json.loads(request.content) == {"body": "Fixture reply"}
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(response_code, json=provider_payload, headers={"Location": "https://example.test/"})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    assert submit_reply("test-only", reservation, "Fixture reply") == expected
    assert len(seen) == 1
