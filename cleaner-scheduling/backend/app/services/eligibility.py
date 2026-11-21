from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Cleaner, CleanerEligibility, Property


def eligible_properties(session: Session, cleaner: Cleaner) -> set[uuid.UUID]:
    if not cleaner.active:
        return set()
    active = set(session.scalars(select(Property.id).where(Property.active.is_(True))))
    rows = session.scalars(select(CleanerEligibility).where(CleanerEligibility.cleaner_id == cleaner.id)).all()
    if cleaner.eligibility_policy == "all_except_exclusions":
        return active - {row.property_id for row in rows if not row.eligible}
    if cleaner.eligibility_policy == "allowlist":
        return active & {row.property_id for row in rows if row.eligible}
    raise ValueError("Cleaner eligibility policy is invalid.")
