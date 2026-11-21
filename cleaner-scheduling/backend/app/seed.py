from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.reservations.fixtures import synthetic_properties
from app.clock import Clock, SystemClock
from app.config import get_settings
from app.db import session_factory
from app.models import OperatorProfile, Property
from app.workspaces import LEGACY_WORKSPACE_ID

SYNTHETIC_OPERATORS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("synthetic-viewer", "Synthetic Viewer", ("viewer",), True),
    ("synthetic-manager", "Synthetic Manager", ("manager",), True),
    ("synthetic-owner", "Synthetic Owner", ("owner",), True),
    ("synthetic-scheduler", "Synthetic Scheduler", ("scheduler",), True),
    ("synthetic-deactivated", "Synthetic Deactivated Owner", ("owner",), False),
)


def seed_operators(session: Session, clock: Clock) -> int:
    inserted = 0
    now = clock.now()
    for subject, display_name, roles, active in SYNTHETIC_OPERATORS:
        existing = session.scalar(select(OperatorProfile).where(OperatorProfile.auth_subject == subject))
        if existing is not None:
            continue
        session.add(
            OperatorProfile(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                auth_subject=subject,
                display_name=display_name,
                roles=list(roles),
                active=active,
            )
        )
        inserted += 1
    return inserted


def seed_properties(session: Session, clock: Clock) -> int:
    inserted = 0
    now = clock.now()
    for row in synthetic_properties():
        provider = str(row["provider"])
        external_id = str(row["external_id"])
        existing = session.scalar(
            select(Property).where(Property.provider == provider, Property.external_id == external_id)
        )
        if existing is not None:
            continue
        session.add(
            Property(
                id=uuid.uuid4(),
                created_at=now,
                updated_at=now,
                version=1,
                provider=provider,
                external_id=external_id,
                name=str(row["name"]),
                timezone="America/New_York",
                bedrooms=int(str(row["bedrooms"])),
                duration_override_minutes=None,
                location_ciphertext=b"synthetic-v1",
                latitude=row["latitude"],
                longitude=row["longitude"],
                geocode_status="synthetic",
                active=True,
            )
        )
        inserted += 1
    return inserted


def main() -> None:
    from app.config import validate_startup
    from app.db import get_engine, validate_app_role

    settings = get_settings()
    validate_startup(settings)
    validate_app_role(get_engine(settings.database_url))

    factory = session_factory(settings, LEGACY_WORKSPACE_ID)
    with factory() as session:
        operators = seed_operators(session, SystemClock())
        properties = seed_properties(session, SystemClock())
        session.commit()
    print(f"seeded {operators} new synthetic operator profiles and {properties} synthetic properties")


if __name__ == "__main__":
    main()
