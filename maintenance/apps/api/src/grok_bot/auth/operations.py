"""Revalidate an active Cleaner session before using Maintenance workspace grants."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from grok_bot.errors import problem

MEMBERSHIP_TOKEN = re.compile(r"opm_[A-Za-z0-9_-]{43}")


class NoOperationsRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def require_active_membership(issuer: str, membership: object, workspace: UUID, account: UUID) -> None:
    origin = urlsplit(issuer)
    if (
        not isinstance(membership, str)
        or not MEMBERSHIP_TOKEN.fullmatch(membership)
        or origin.scheme != "https"
        or not origin.hostname
        or any((origin.username, origin.password, origin.path, origin.query, origin.fragment))
    ):
        problem(401, "UNAUTHENTICATED", "Sign in with Cleaner again.")
    request = Request(
        issuer + "/auth/operations/membership",
        headers={"Authorization": "Bearer " + membership, "Accept": "application/json"},
    )
    try:
        with build_opener(NoOperationsRedirect()).open(request, timeout=6) as response:
            identity = json.loads(response.read(4096))
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            problem(401, "UNAUTHENTICATED", "Sign in with Cleaner again.")
        problem(503, "DEPENDENCY_UNAVAILABLE", "Cleaner membership is temporarily unavailable.")
    except (URLError, OSError, ValueError):
        problem(503, "DEPENDENCY_UNAVAILABLE", "Cleaner membership is temporarily unavailable.")
    if not isinstance(identity, dict) or identity.get("workspace_id") != str(workspace) or identity.get("account_id") != str(account):
        problem(401, "UNAUTHENTICATED", "Sign in with Cleaner again.")
