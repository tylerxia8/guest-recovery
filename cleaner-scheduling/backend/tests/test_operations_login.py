import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.api.routes import accounts
from app.domain.errors import AppError
from app.services.operations_access import read_session_digest


def test_operations_grant_is_bound_to_client_and_verifier_and_consumed(monkeypatch):
    identity = SimpleNamespace(id=uuid4(), workspace_id=uuid4())
    authenticate = Mock(return_value=identity)
    revalidate = Mock(return_value=identity)
    monkeypatch.setattr(accounts, "authenticate_session", authenticate)
    monkeypatch.setattr(accounts, "authenticate_session_digest", revalidate)
    monkeypatch.setattr(accounts, "account_rate_limit", Mock())
    cache = Mock()
    saved = {}

    def save(key, value, *, ex, nx):
        assert ex in (60, 900) and nx is True
        saved[key] = value
        return True

    cache.set.side_effect = save
    cache.getdel.side_effect = lambda key: saved.pop(key, None)
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    settings = SimpleNamespace(
        operations_login_enabled=True, operations_touchup_enabled=False, operations_repair_enabled=False
    )
    clock = SimpleNamespace(now=lambda: datetime.now(UTC))
    request = SimpleNamespace(headers={"authorization": "Bearer manager-session"})
    session = Mock()
    result = accounts.issue_operations_grant(
        accounts.OperationsGrantWrite(client="guest-recovery", challenge=challenge),
        request,
        session,
        settings,
        clock,
        cache,
    )
    code = json.loads(result.body)["code"]
    assert result.headers["Cache-Control"] == "no-store"
    assert list(saved.values()) == [accounts.token_digest("manager-session")]

    for client, candidate in (("maintenance", verifier), ("guest-recovery", secrets.token_urlsafe(32))):
        with pytest.raises(AppError, match="Shared sign-in expired"):
            accounts.redeem_operations_grant(
                accounts.OperationsRedeemWrite(client=client, code=code, verifier=candidate),
                session,
                settings,
                clock,
                cache,
            )
    revalidate.assert_not_called()
    valid = accounts.OperationsRedeemWrite(client="guest-recovery", code=code, verifier=verifier)
    result = accounts.redeem_operations_grant(valid, session, settings, clock, cache)
    payload = json.loads(result.body)
    assert payload["account_id"] == str(identity.id)
    assert payload["workspace_id"] == str(identity.workspace_id)
    assert payload["read_token_expires_in"] == 900
    cache.get.side_effect = saved.get
    assert accounts.token_digest("manager-session") == read_session_digest(cache, payload["read_token"])
    assert all(payload["read_token"] not in key for key in saved)
    assert revalidate.call_args.args[1] == accounts.token_digest("manager-session")
    with pytest.raises(AppError, match="Shared sign-in expired"):
        accounts.redeem_operations_grant(valid, session, settings, clock, cache)


@pytest.mark.parametrize(
    "method,path,enabled,prefix",
    [
        ("POST", "/v1/workspace/operations/stay-context", True, b"ops_"),
        ("POST", "/v1/workspace/operations/cleaning-touchup", True, b"ops_"),
        ("GET", "/v1/workspace/operations/stays", True, b"opc_"),
        ("GET", "/v1/workspace/setup", True, b"ops_"),
        ("GET", "/v1/workspace/operations/stay-context", False, b"ops_"),
    ],
)
def test_operations_read_token_cannot_be_used_outside_its_scope(monkeypatch, method, path, enabled, prefix):
    from app import db
    from app.api import deps
    from starlette.requests import Request

    monkeypatch.setattr(deps, "enforce_rate_limit", Mock())
    lookup = Mock()
    monkeypatch.setattr(deps, "read_session_digest", lookup)
    catalog = Mock()
    monkeypatch.setattr(db, "catalog_session_factory", catalog)
    settings = SimpleNamespace(
        workspace_signup_enabled=True, operations_login_enabled=enabled, operations_touchup_enabled=True
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, session_factory=Mock(), redis=Mock(), clock=Mock()))
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "app": app,
            "headers": [(b"authorization", b"Bearer " + prefix + b"a" * 43)],
        }
    )
    with pytest.raises(AppError, match="Sign in to continue"):
        next(deps.get_db(request))
    lookup.assert_not_called()
    catalog.assert_not_called()


def test_operations_read_revalidates_the_source_session_before_opening_workspace(monkeypatch):
    from contextlib import nullcontext

    from app import db
    from app.api import deps
    from app.services import workspace_accounts
    from starlette.requests import Request

    monkeypatch.setattr(deps, "enforce_rate_limit", Mock())
    digest = "a" * 64
    monkeypatch.setattr(deps, "read_session_digest", Mock(return_value=digest))
    catalog = Mock()
    monkeypatch.setattr(db, "catalog_session_factory", lambda _: lambda: nullcontext(catalog))
    factory = Mock()
    monkeypatch.setattr(db, "session_factory", factory)
    revalidate = Mock(side_effect=AppError("invalid_session", "Sign in to continue.", 401))
    monkeypatch.setattr(workspace_accounts, "authenticate_session_digest", revalidate)
    settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)
    clock = Mock()
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, session_factory=Mock(), redis=Mock(), clock=clock))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/workspace/operations/stay-context",
            "app": app,
            "headers": [(b"authorization", b"Bearer ops_" + b"a" * 43)],
        }
    )
    with pytest.raises(AppError, match="Sign in to continue"):
        next(deps.get_db(request))
    revalidate.assert_called_once_with(catalog, digest, clock.now.return_value)
    factory.assert_not_called()


def test_operations_read_selects_workspace_from_the_validated_account(monkeypatch):
    from contextlib import nullcontext

    from app import db
    from app.api import deps
    from app.services import workspace_accounts
    from starlette.requests import Request

    monkeypatch.setattr(deps, "enforce_rate_limit", Mock())
    monkeypatch.setattr(deps, "read_session_digest", Mock(return_value="a" * 64))
    monkeypatch.setattr(db, "catalog_session_factory", lambda _: lambda: nullcontext(Mock()))
    identity = SimpleNamespace(id=uuid4(), workspace_id=uuid4())
    monkeypatch.setattr(workspace_accounts, "authenticate_session_digest", Mock(return_value=identity))
    workspace_session = Mock()
    factory = Mock(return_value=Mock(return_value=workspace_session))
    monkeypatch.setattr(db, "session_factory", factory)
    settings = SimpleNamespace(workspace_signup_enabled=True, operations_login_enabled=True)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings, session_factory=Mock(), workspace_factories={}, redis=Mock(), clock=Mock()
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/workspace/operations/stay-context",
            "app": app,
            "headers": [(b"authorization", b"Bearer ops_" + b"a" * 43)],
            "query_string": f"workspace_id={uuid4()}".encode(),
        }
    )
    sessions = deps.get_db(request)
    assert next(sessions) is workspace_session
    sessions.close()
    factory.assert_called_once_with(settings, workspace_id=identity.workspace_id)
    assert request.state.account_subject == workspace_accounts.account_subject(identity)
    workspace_session.close.assert_called_once()
