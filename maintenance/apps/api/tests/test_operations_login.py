import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from grok_bot.api.routes import auth
from starlette.exceptions import HTTPException
from starlette.requests import Request


def settings_for_login():
    return SimpleNamespace(
        allows_local_auth=True,
        operations_workspace_provisioning=False,
        operations_turnover_origin="https://turnover.example",
        operations_workspace_id=uuid4(),
        operations_account_id=uuid4(),
        operations_organization_id=uuid4(),
        operations_maintenance_user_id=uuid4(),
        session_secret="fixture-only-secret-value-not-a-credential",
        app_env="test",
        local_token_ttl_seconds=60,
    )


@pytest.mark.parametrize("matching,manager", [(False, True), (True, False), (True, True)])
def test_operations_exchange_requires_linked_manager(monkeypatch, matching, manager):
    settings = settings_for_login()
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(settings=settings))})
    identity = {
        "workspace_id": str(settings.operations_workspace_id if matching else uuid4()),
        "account_id": str(settings.operations_account_id),
    }
    response = Mock()
    response.read.return_value = json.dumps(identity).encode()
    open_request = Mock(return_value=nullcontext(response))
    monkeypatch.setattr(auth, "build_opener", lambda _: SimpleNamespace(open=open_request))
    principal = Mock(
        return_value=SimpleNamespace(
            roles=("property_manager",) if manager else ("tenant",),
            user_id=settings.operations_maintenance_user_id,
            organization_id=settings.operations_organization_id,
            display_name="Fixture Manager",
        )
    )
    monkeypatch.setattr(auth, "principal_from_request", principal)
    body = auth.OperationsLoginBody(code="a" * 43, verifier="b" * 43)
    if matching and manager:
        result = auth.operations_login(body, request, Mock())
        assert result["organization_id"] == str(settings.operations_organization_id)
        assert result["token"].startswith("v1.")
    else:
        with pytest.raises(HTTPException) as error:
            auth.operations_login(body, request, Mock())
        assert error.value.status_code == 403
    assert principal.called == matching
    assert json.loads(open_request.call_args.args[0].data)["client"] == "maintenance"


def test_operations_exchange_is_unavailable_outside_local_auth(monkeypatch):
    settings = settings_for_login()
    settings.allows_local_auth = False
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(settings=settings))})
    opener = Mock()
    monkeypatch.setattr(auth, "build_opener", opener)
    with pytest.raises(HTTPException) as error:
        auth.operations_login(auth.OperationsLoginBody(code="a" * 43, verifier="b" * 43), request, Mock())
    assert error.value.status_code == 404
    opener.assert_not_called()
