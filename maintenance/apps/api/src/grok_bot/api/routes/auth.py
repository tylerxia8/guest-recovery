import json
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request as UrlRequest, build_opener
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from grok_bot.api.deps import get_principal, get_session, principal_from_request, request_id_of
from grok_bot.api.schemas import LoginBody
from grok_bot.auth.constants import LOCAL_DEV_ISSUER
from grok_bot.auth.permissions import Principal
from grok_bot.auth.tokens import mint_local_token
from grok_bot.domain.distributions import record_step_up
from grok_bot.errors import problem
from grok_bot.api.success import ERROR_RESPONSES, LoginOut, StepUpOut

router = APIRouter(responses=ERROR_RESPONSES)


@router.post("/auth/local/login", response_model=LoginOut)
def local_login(body: LoginBody, request: Request, session: Session = Depends(get_session, scope="function")) -> dict:
    settings = request.app.state.settings
    if not settings.allows_local_auth:
        problem(404, "NOT_FOUND", "Not found.")
    rows = session.execute(
        text(
            """
            SELECT user_id, organization_id, display_name, role
            FROM app_local_login(:issuer, :subject, :password)
            """
        ),
        {"issuer": LOCAL_DEV_ISSUER, "subject": body.subject, "password": body.password},
    ).mappings().all()
    organizations = {row["organization_id"] for row in rows}
    if len(organizations) > 1:
        problem(
            409,
            "AMBIGUOUS_IDENTITY",
            "This subject matches more than one organization. An administrator must resolve it.",
        )
    if len(organizations) != 1:
        problem(401, "UNAUTHENTICATED", "Subject or password is incorrect.")
    first = rows[0]
    token = mint_local_token(
        secret=settings.session_secret,
        user_id=first["user_id"],
        organization_id=first["organization_id"],
        environment=settings.app_env,
        ttl_seconds=settings.local_token_ttl_seconds,
    )
    return {
        "token": token,
        "token_type": "bearer",
        "auth_method": "local_dev",
        "production_oidc": False,
        "user_id": str(first["user_id"]),
        "organization_id": str(first["organization_id"]),
        "display_name": first["display_name"],
        "request_id": request_id_of(request),
    }


@router.post("/auth/local/step-up", response_model=StepUpOut)
def local_step_up(body: LoginBody, request: Request, principal: Principal = Depends(get_principal), session: Session = Depends(get_session, scope="function")) -> dict:
    if not request.app.state.settings.allows_local_auth:
        problem(404, "NOT_FOUND", "Not found.")
    if body.subject != "" and body.password == "":
        problem(401, "UNAUTHENTICATED", "Step-up requires the local password.")
    checked = session.execute(
        text("SELECT user_id FROM app_local_login(:issuer, :subject, :password)"),
        {"issuer": LOCAL_DEV_ISSUER, "subject": body.subject, "password": body.password},
    ).scalar()
    if checked != principal.user_id:
        problem(401, "UNAUTHENTICATED", "Step-up requires the local password.")
    result = record_step_up(session, principal.organization_id, principal.user_id, request.app.state.clock.now())
    result["request_id"] = request_id_of(request)
    return result


class OperationsLoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)


class NoOperationsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@router.post("/auth/operations/redeem", response_model=LoginOut)
def operations_login(
    body: OperationsLoginBody, request: Request, session: Session = Depends(get_session, scope="function")
) -> dict:
    settings = request.app.state.settings
    issuer = urlsplit(settings.operations_turnover_origin)
    workspace = settings.operations_workspace_id
    account = settings.operations_account_id
    organization = settings.operations_organization_id
    user = settings.operations_maintenance_user_id
    if (
        not settings.allows_local_auth
        or (not settings.operations_workspace_provisioning and not all((workspace, account, organization, user)))
        or issuer.scheme != "https"
        or not issuer.hostname
        or any((issuer.username, issuer.password, issuer.path, issuer.query, issuer.fragment))
    ):
        problem(404, "NOT_FOUND", "Shared sign-in is unavailable.")
    payload = json.dumps({"client": "maintenance", "code": body.code, "verifier": body.verifier}).encode()
    upstream = UrlRequest(
        issuer.geturl() + "/auth/operations/redeem", data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with build_opener(NoOperationsRedirect()).open(upstream, timeout=8) as response:
            identity = json.loads(response.read(4096))
    except (URLError, OSError, ValueError):
        problem(401, "UNAUTHENTICATED", "Shared sign-in could not be completed.")
    if not isinstance(identity, dict):
        problem(401, "UNAUTHENTICATED", "Shared sign-in could not be completed.")
    if settings.operations_workspace_provisioning:
        try:
            organization = UUID(str(identity.get("workspace_id")))
            user = UUID(str(identity.get("account_id")))
        except ValueError:
            problem(401, "UNAUTHENTICATED", "Shared sign-in did not return a workspace.")
        provisioned = session.scalar(
            text("SELECT public.app_provision_operations_workspace(:workspace, :account, :grant_id)"),
            {"workspace": organization, "account": user, "grant_id": uuid4()},
        )
        if not provisioned:
            problem(403, "FORBIDDEN", "This Lucena account is not linked to Maintenance.")
    elif identity.get("workspace_id") != str(workspace) or identity.get("account_id") != str(account):
        problem(403, "FORBIDDEN", "This Lucena account is not linked to Maintenance.")

    token = mint_local_token(
        secret=settings.session_secret,
        user_id=user,
        organization_id=organization,
        environment=settings.app_env,
        ttl_seconds=settings.local_token_ttl_seconds,
    )
    # Resolve the existing user and current grants through the same checks as a normal session.
    scope = {**request.scope, "headers": [(b"authorization", ("Bearer " + token).encode("ascii"))]}
    principal = principal_from_request(Request(scope), session)
    if "property_manager" not in principal.roles:
        problem(403, "FORBIDDEN", "Maintenance manager access is required.")
    return {
        "token": token,
        "token_type": "bearer",
        "auth_method": "local_dev",
        "production_oidc": False,
        "user_id": str(principal.user_id),
        "organization_id": str(principal.organization_id),
        "display_name": principal.display_name,
        "request_id": request_id_of(request),
    }
