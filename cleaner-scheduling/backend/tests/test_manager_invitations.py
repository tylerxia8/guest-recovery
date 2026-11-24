import base64
import hashlib
import secrets
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
    hosted = settings.model_copy(
        update={
            "workspace_signup_enabled": True,
            "workspace_registration_enabled": True,
            "operations_login_enabled": True,
        }
    )
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

        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        grant = client.post(
            "/v1/accounts/operations/grants",
            headers=auth(member),
            json={"client": "maintenance", "challenge": challenge},
        )
        assert grant.status_code == 200, grant.text
        exchange = client.post(
            "/v1/accounts/operations/redeem",
            json={"client": "maintenance", "code": grant.json()["code"], "verifier": verifier},
        )
        assert exchange.status_code == 200, exchange.text
        claims = exchange.json()
        membership_headers = {"Authorization": "Bearer " + claims["membership_token"]}
        membership_url = "/v1/accounts/operations/membership"
        assert client.get(membership_url, headers=membership_headers).json() == {
            "workspace_id": str(workspace_id),
            "account_id": claims["account_id"],
        }

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

        assert (
            client.post("/v1/workspace/complete", headers={**auth(member), "Idempotency-Key": "test"}).status_code
            == 403
        )
        managers = client.get("/v1/workspace/managers", headers=auth(owner)).json()["managers"]
        assert [row["email"] for row in managers] == ["member@example.test"]
        inbox = "/v1/workspace/action-inbox"
        claim_path = inbox + "/claim"
        member_subject = "manager:" + managers[0]["id"]
        candidate = {
            "source": "maintenance",
            "item_id": str(uuid.uuid4()),
            "assignee_subject": member_subject,
            "version": 0,
        }
        assert client.patch(claim_path, headers=auth(other), json=candidate).status_code == 422
        claim = client.patch(claim_path, headers=auth(member), json=candidate)
        assert claim.status_code == 200, claim.text
        assert client.patch(claim_path, headers=auth(owner), json=candidate).status_code == 409
        assert client.get(inbox, headers=auth(other)).json()["claims"] == []
        shared_queue = client.get(inbox, headers=auth(owner)).json()
        assert shared_queue["claims"][0]["assignee_subject"] == member_subject
        handed_off = client.patch(
            claim_path,
            headers=auth(member),
            json={
                **candidate,
                "version": 1,
                "assignee_subject": shared_queue["current_manager"],
            },
        )
        assert handed_off.status_code == 200, handed_off.text
        assert handed_off.json()["version"] == 2
        queue_grant = client.post("/v1/accounts/operations/inbox-grant", headers=auth(member))
        assert queue_grant.status_code == 200, queue_grant.text
        queue_headers = {"Authorization": "Bearer " + queue_grant.json()["token"]}
        queue_identity = "/v1/accounts/operations/inbox-identity"
        assert client.get(queue_identity, headers=queue_headers).json()["workspace_id"] == str(workspace_id)
        assert client.get(membership_url, headers=queue_headers).status_code == 401

        member_url = f"/v1/workspace/managers/{managers[0]['id']}"
        assert client.delete(member_url, headers=auth(other)).status_code == 404
        assert claims["account_id"] == managers[0]["id"]
        assert claims["workspace_owner_id"] != claims["account_id"]
        another_invite = client.post(
            "/v1/workspace/manager-invitations", headers=auth(other), json={"email": "member@example.test"}
        )
        assert another_invite.status_code == 201
        a_second_company = client.post(
            "/v1/accounts/join",
            headers=auth(member),
            json={"invitation_token": another_invite.json()["invitation_token"]},
        )
        assert a_second_company.status_code == 200, a_second_company.text
        assert client.delete(member_url, headers=auth(owner)).status_code == 200
        assert client.get(membership_url, headers=membership_headers).status_code == 401
        assert client.get(queue_identity, headers=queue_headers).status_code == 401
        assert client.get("/v1/accounts/me", headers=auth(member)).status_code == 401
        assert client.get("/v1/accounts/me", headers=auth(login.json())).status_code == 401
        assert sign_up("member@example.test", code).status_code in {403, 409}
        login_after_removal = client.post(
            "/v1/accounts/login",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"email": "member@example.test", "password": "a-test-only-password"},
        )
        assert login_after_removal.status_code == 200, login_after_removal.text
        assert login_after_removal.json()["workspace_id"] == other["workspace_id"]
        assert client.get("/v1/workspace/setup", headers=auth(owner)).json()["completed"] is True

        existing_invite = client.post(
            "/v1/workspace/manager-invitations", headers=auth(owner), json={"email": "other@example.test"}
        )
        assert existing_invite.status_code == 201, existing_invite.text
        invite_body = {"invitation_token": existing_invite.json()["invitation_token"]}
        assert client.post("/v1/accounts/join", headers=auth(owner), json=invite_body).status_code == 403
        joined = client.post("/v1/accounts/join", headers=auth(other), json=invite_body)
        assert joined.status_code == 200, joined.text
        assert client.post("/v1/accounts/join", headers=auth(other), json=invite_body).status_code == 403
        workspaces = client.get("/v1/accounts/workspaces", headers=auth(other)).json()
        assert workspaces["current"] == other["workspace_id"]
        assert {item["id"] for item in workspaces["items"]} == {owner["workspace_id"], other["workspace_id"]}
        switch_path = "/v1/accounts/switch-workspace"
        assert (
            client.post(switch_path, headers=auth(other), json={"workspace_id": str(uuid.uuid4())}).status_code == 403
        )
        switched = client.post(switch_path, headers=auth(other), json={"workspace_id": owner["workspace_id"]})
        assert switched.status_code == 200, switched.text
        alias = switched.json()
        assert alias["workspace_owner"] is False and alias["primary_login"] is False
        assert alias["email"] == "other@example.test"
        assert client.get("/v1/accounts/me", headers=auth(other)).status_code == 401
        assert client.get(inbox, headers=auth(alias)).json()["claims"][0]["version"] == 2
        returned = client.post(switch_path, headers=auth(alias), json={"workspace_id": other["workspace_id"]})
        assert returned.status_code == 200, returned.text
        assert returned.json()["workspace_owner"] is True
        assert client.get("/v1/accounts/me", headers=auth(alias)).status_code == 401
        teammate = client.get("/v1/workspace/managers", headers=auth(owner)).json()["managers"][0]
        assert teammate["email"] == "other@example.test"
        assert client.delete("/v1/workspace/managers/" + teammate["id"], headers=auth(owner)).status_code == 200
        assert client.get("/v1/accounts/me", headers=auth(returned.json())).status_code == 200
        assert (
            client.post(
                switch_path, headers=auth(returned.json()), json={"workspace_id": owner["workspace_id"]}
            ).status_code
            == 403
        )
