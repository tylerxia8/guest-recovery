import uuid

import pytest
from app.api.app_factory import create_app
from app.clock import FrozenClock
from app.config import Settings
from app.db import session_factory
from app.models.workspace_connection import WorkspaceConnection
from app.services.field_cipher import protect_text
from fastapi.testclient import TestClient


def test_invited_manager_shares_connection_and_can_be_removed(
    settings: Settings, clock: FrozenClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosted = settings.model_copy(update={"workspace_signup_enabled": True, "workspace_registration_enabled": True})
    # Exercise real API authorization with loopback-only test services.
    monkeypatch.setattr("app.api.app_factory.validate_startup", lambda _: None)
    application = create_app(hosted, clock)

    def sign_up(email: str, invite: str | None = None, key: str | None = None):
        payload = {"email": email, "name": "Manager", "password": "a-test-only-password"}
        if invite is not None:
            payload["invitation_token"] = invite
        return client.post("/v1/accounts/signup", headers={"Idempotency-Key": key or str(uuid.uuid4())}, json=payload)

    def auth(account: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {account['token']}"}

    with TestClient(application) as client:
        owner_response = sign_up("owner@example.test")
        assert owner_response.status_code == 201, owner_response.text
        owner = owner_response.json()
        other = sign_up("other@example.test").json()
        hosted.workspace_registration_enabled = False
        workspace_id = uuid.UUID(owner["workspace_id"])
        with session_factory(hosted, workspace_id)() as session:
            session.add(
                WorkspaceConnection(
                    workspace_id=workspace_id,
                    token_ciphertext=protect_text("fictional-shared-token", hosted.field_encryption_secret),
                    connected_at=clock.now(),
                    completed_at=clock.now(),
                )
            )
            session.commit()

        invited = client.post(
            "/v1/workspace/manager-invitations", headers=auth(owner), json={"email": "member@example.test"}
        )
        assert invited.status_code == 201, invited.text
        code = invited.json()["invitation_token"]
        assert sign_up("wrong@example.test", code).status_code == 403
        key = str(uuid.uuid4())
        response = sign_up("member@example.test", code, key)
        assert response.status_code == 201, response.text
        member = response.json()
        assert member["workspace_id"] == str(workspace_id)
        assert member["workspace_owner"] is False
        assert sign_up("member@example.test", code, key).json() == member
        assert sign_up("member@example.test", code).status_code in {403, 409}

        login = client.post(
            "/v1/accounts/login",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"email": "member@example.test", "password": "a-test-only-password"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["workspace_id"] == str(workspace_id)
        assert login.json()["token"] != member["token"]

        setup = client.get("/v1/workspace/setup", headers=auth(member)).json()
        assert setup["completed"] is True
        assert setup["can_manage_connection"] is False
        assert "fictional-shared-token" not in str(setup)
        assert (
            client.post(
                "/v1/workspace/manager-invitations", headers=auth(member), json={"email": "third@example.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/workspace/hospitable",
                headers={**auth(member), "Idempotency-Key": "test"},
                json={"token": "fictional-replacement"},
            ).status_code
            == 403
        )

        managers = client.get("/v1/workspace/managers", headers=auth(owner)).json()["managers"]
        assert [row["email"] for row in managers] == ["member@example.test"]
        member_url = f"/v1/workspace/managers/{managers[0]['id']}"
        assert client.delete(member_url, headers=auth(other)).status_code == 404
        assert client.delete(member_url, headers=auth(owner)).status_code == 200
        assert client.get("/v1/accounts/me", headers=auth(member)).status_code == 401
        assert client.get("/v1/accounts/me", headers=auth(login.json())).status_code == 401
        assert sign_up("member@example.test", code).status_code == 403
        assert client.get("/v1/workspace/setup", headers=auth(owner)).json()["completed"] is True
