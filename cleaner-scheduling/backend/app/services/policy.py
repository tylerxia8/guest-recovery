from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.errors import AppError
from app.domain.policy_schema import PolicySettings
from app.domain.timeutil import dump_utc
from app.models import PolicyVersion


def serialize_policy(policy: PolicyVersion) -> dict[str, object]:
    return {
        "id": str(policy.id),
        "version": policy.version,
        "created_at": dump_utc(policy.created_at),
        "updated_at": dump_utc(policy.updated_at),
        "version_number": policy.version_number,
        "settings": policy.settings,
        "approved_at": dump_utc(policy.approved_at) if policy.approved_at else None,
        "approved_by": policy.approved_by,
        "effective_at": dump_utc(policy.effective_at) if policy.effective_at else None,
        "is_active": policy.is_active,
    }


def create_policy(
    session: Session,
    *,
    clock: Clock,
    settings: PolicySettings,
    author_subject: str,
) -> PolicyVersion:
    del author_subject
    now = clock.now()
    session.execute(text("SELECT pg_advisory_xact_lock(84123001)"))
    current = session.scalar(
        select(PolicyVersion.version_number).order_by(PolicyVersion.version_number.desc()).limit(1)
    )
    version_number = int(current or 0) + 1
    policy = PolicyVersion(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        version=1,
        version_number=version_number,
        settings=cast(dict[str, object], settings.model_dump(mode="json")),
        approved_by=None,
        approved_at=None,
        effective_at=None,
        is_active=False,
    )
    session.add(policy)
    session.flush()
    return policy


def approve_policy(
    session: Session,
    *,
    clock: Clock,
    policy_id: uuid.UUID,
    expected_version: int,
    approver: str,
    effective_at: datetime,
) -> PolicyVersion:
    now = clock.now()
    policy = session.scalar(select(PolicyVersion).where(PolicyVersion.id == policy_id).with_for_update())
    if policy is None:
        raise AppError("not_found", "Policy version was not found.", 404)
    if policy.version != expected_version:
        raise AppError("stale_version", "Policy version is stale.", 409)
    if policy.approved_at is not None:
        return policy
    aware_effective = effective_at.astimezone(now.tzinfo)
    session.execute(
        update(PolicyVersion)
        .where(PolicyVersion.is_active.is_(True), PolicyVersion.id != policy.id)
        .values(is_active=False, version=PolicyVersion.version + 1, updated_at=now)
    )
    session.flush()
    policy.approved_by = approver
    policy.approved_at = now
    policy.effective_at = aware_effective
    policy.is_active = True
    policy.version = policy.version + 1
    policy.updated_at = now
    session.flush()
    return policy


def active_policy_exists(session: Session) -> bool:
    found = session.scalar(
        select(PolicyVersion.id)
        .where(PolicyVersion.is_active.is_(True), PolicyVersion.approved_at.is_not(None))
        .limit(1)
    )
    return found is not None
