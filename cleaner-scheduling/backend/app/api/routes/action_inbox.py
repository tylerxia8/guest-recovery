from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from app.api.deps import get_clock, get_db, require_manager
from app.clock import Clock
from app.domain.errors import AppError
from app.domain.principal import Principal
from app.models import CleaningTask, OperationalException, OperatorProfile, Property
from app.services.audit import write_audit
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/v1/workspace/action-inbox", include_in_schema=False)


@router.get("")
def inbox(
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    rows = session.execute(
        select(OperationalException, CleaningTask.deadline_at, Property.name)
        .outerjoin(CleaningTask, CleaningTask.id == OperationalException.task_id)
        .outerjoin(Property, Property.id == CleaningTask.property_id)
        .where(OperationalException.status.in_(("open", "acknowledged")))
        .order_by(CleaningTask.deadline_at.asc().nulls_last(), OperationalException.opened_at.desc())
        .limit(100)
    ).all()
    managers = session.scalars(
        select(OperatorProfile)
        .where(OperatorProfile.active.is_(True), OperatorProfile.roles.overlap(["manager", "owner"]))
        .order_by(OperatorProfile.display_name, OperatorProfile.auth_subject)
    ).all()
    claims = session.execute(
        text("""
        SELECT c.source, c.item_id, p.auth_subject AS assignee_subject, c.version
        FROM action_inbox_claims c
        LEFT JOIN operator_profiles p ON p.workspace_id=c.workspace_id AND p.auth_subject=c.assignee_subject
          AND p.active AND p.roles && ARRAY['manager','owner']::text[]
    """)
    ).mappings()
    return {
        "items": [
            {
                "id": row.id,
                "title": row.code.replace("_", " ").capitalize(),
                "next_action": "Review cleaning exception",
                "property": property_name,
                "due_at": due,
                "urgent": row.severity == "critical",
                "path": "/exceptions",
            }
            for row, due, property_name in rows
        ],
        "managers": [{"subject": m.auth_subject, "name": m.display_name} for m in managers],
        "current_manager": principal.subject,
        "claims": [dict(claim) for claim in claims],
    }


class ClaimWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["cleaner", "guest-replies", "supplies", "maintenance"]
    item_id: UUID
    assignee_subject: str | None = Field(max_length=120)
    version: int = Field(ge=0)


@router.patch("/claim")
def change_claim(
    payload: ClaimWrite,
    principal: Principal = Depends(require_manager),
    session: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    workspace = session.info["workspace_id"]
    if (
        payload.assignee_subject is not None
        and session.scalar(
            select(OperatorProfile.id).where(
                OperatorProfile.auth_subject == payload.assignee_subject,
                OperatorProfile.active.is_(True),
                OperatorProfile.roles.overlap(["manager", "owner"]),
            )
        )
        is None
    ):
        raise AppError("assignee_unavailable", "Choose an active manager in this workspace.", 422)
    params = {
        "workspace": workspace,
        "source": payload.source,
        "item": payload.item_id,
        "assignee": payload.assignee_subject,
        "version": payload.version,
    }
    if payload.version == 0:
        statement = """
            INSERT INTO action_inbox_claims (workspace_id, source, item_id, assignee_subject, version)
            VALUES (:workspace, :source, :item, :assignee, 1)
            ON CONFLICT (workspace_id, source, item_id) DO NOTHING
            RETURNING version
        """
    else:
        statement = """
            UPDATE action_inbox_claims SET assignee_subject=:assignee, version=version+1
            WHERE workspace_id=:workspace AND source=:source AND item_id=:item AND version=:version
            RETURNING version
        """
    version = session.scalar(text(statement), params)
    if version is None:
        raise AppError("stale_claim", "This item was updated by another manager. Refresh and try again.", 409)
    write_audit(
        session,
        clock=clock,
        actor_type="operator",
        actor_subject=principal.subject,
        action="action_inbox.assignment_changed",
        entity_type="action_inbox_item",
        entity_id=payload.item_id,
        correlation_id=uuid4(),
        reason=None,
        redacted_changes={"source": payload.source, "assignee_subject": payload.assignee_subject, "version": version},
        request_id=None,
    )
    session.commit()
    return {"version": version, "assignee_subject": payload.assignee_subject}
