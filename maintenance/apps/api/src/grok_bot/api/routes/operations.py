from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from grok_bot.api.deps import get_principal, get_session
from grok_bot.auth.operations import read_operations_identity
from grok_bot.auth.permissions import Principal
from grok_bot.db import set_tenant
from grok_bot.errors import problem
from grok_bot.api.success import ERROR_RESPONSES, OperationsQueueOut

router = APIRouter(responses=ERROR_RESPONSES)


@router.get("/operations/queue", response_model=OperationsQueueOut)
def get_queue(principal: Principal = Depends(get_principal), session: Session = Depends(get_session, scope="function")):
    if not (principal.allows("distributions.approve") or principal.allows("receipts.read")):
        problem(403, "FORBIDDEN", "You do not have access to this action.")
    organization_id = principal.organization_id
    dead = _count(session, "SELECT count(*) FROM outbox_events WHERE organization_id = :organization_id AND status = 'FAILED'", organization_id)
    unknown = _count(
        session,
        "SELECT count(*) FROM distribution_items WHERE organization_id = :organization_id AND status = 'UNKNOWN'",
        organization_id,
    )
    expiring = _count(
        session,
        """
        SELECT count(*) FROM distribution_batches
        WHERE organization_id = :organization_id AND status = 'APPROVED' AND expires_at IS NOT NULL
        """,
        organization_id,
    )
    unassigned = _count(
        session,
        """
        SELECT count(*) FROM maintenance_tickets AS t
        WHERE t.organization_id = :organization_id AND t.emergency IS TRUE
          AND NOT EXISTS (
            SELECT 1 FROM assignments AS a
            WHERE a.organization_id = t.organization_id AND a.ticket_id = t.id
          )
        """,
        organization_id,
    )
    return {
        "dead_letters": dead,
        "unknown_payments": unknown,
        "approved_batches": expiring,
        "unassigned_emergency_tickets": unassigned,
        "alerts_sent": False,
        "delivery_enabled": False,
    }


def _count(session, sql: str, organization_id) -> int:
    return int(session.execute(text(sql), {"organization_id": organization_id}).scalar() or 0)

@router.get("/operations/action-inbox", include_in_schema=False)
def action_inbox(request: Request, session: Session = Depends(get_session, scope="function")):
    settings = request.app.state.settings
    if not settings.allows_operations_membership:
        problem(404, "NOT_FOUND", "Shared Maintenance is unavailable.")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        problem(401, "UNAUTHENTICATED", "Shared inbox access is required.")
    identity = read_operations_identity(settings.operations_turnover_origin, authorization[7:], inbox=True)
    try:
        workspace, account, owner = (UUID(identity[key]) for key in ("workspace_id", "account_id", "workspace_owner_id"))
    except (ValueError, KeyError, TypeError, AttributeError):
        problem(401, "UNAUTHENTICATED", "Sign in with Cleaner again.")
    set_tenant(session, workspace, account)
    if session.scalar(text("SELECT settings ->> 'lucena_account_id' FROM organizations WHERE id=:org"), {"org": workspace}) != str(owner):
        return {"items": []}
    rows = session.execute(text("""
        SELECT t.id, t.ticket_number, t.status, t.emergency, t.urgent_triage, p.name,
               a.status AS assignment_status
        FROM maintenance_tickets t
        LEFT JOIN assignments a ON a.organization_id=t.organization_id AND a.id=t.active_assignment_id
        LEFT JOIN units u ON u.organization_id=t.organization_id AND u.id=t.unit_id
        LEFT JOIN properties p ON p.organization_id=u.organization_id AND p.id=u.property_id
        WHERE t.organization_id=:org AND (
            t.status IN ('ASSIGNMENT_PENDING', 'COMPLETION_REVIEW') OR
            ((t.emergency OR t.urgent_triage) AND t.status NOT IN ('COMPLETED', 'CLOSED', 'CANCELED')
              AND (a.status IS NULL OR a.status <> 'ACCEPTED'))
        )
        ORDER BY t.emergency DESC, t.urgent_triage DESC, t.created_at, t.id
        LIMIT 100
    """), {"org": workspace}).mappings()
    return {"items": [{
        "id": row["id"],
        "title": f"Repair #{row['ticket_number']}",
        "next_action": (
            "Review completed repair" if row["status"] == "COMPLETION_REVIEW" else
            "Follow up with the repair worker" if row["assignment_status"] == "PENDING" else
            "Assign an available repair worker"
        ),
        "property": row["name"],
        "due_at": None,
        "urgent": row["emergency"] or row["urgent_triage"],
        "path": "/maintenance",
    } for row in rows]}
