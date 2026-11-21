from __future__ import annotations

from datetime import UTC, datetime

from app.clock import FrozenClock
from app.config import get_settings
from app.db import session_factory
from app.models import Assignment
from app.workspaces import LEGACY_WORKSPACE_ID
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_scheduling import (
    _approve,
    _available,
    _cleaner,
    _preview,
    _publish,
    _sync,
    _turnover,
)
from tests.test_sms import _inbound, _opt_in


def db():
    return session_factory(get_settings(), LEGACY_WORKSPACE_ID)()


def complete_fixture(client: TestClient, *, two: bool = False) -> str:
    _approve(client)
    stays = _turnover("R-A", "P-A", "2026-11-10")
    if two:
        stays += _turnover("R-B", "P-B", "2026-11-10")
    _sync(client, stays, "completion-sync")
    cleaner = _cleaner(
        client, name="Synthetic Cleaner", phone="+15555550401", properties=["P-A", "P-B"], key="completion-cleaner"
    )
    _opt_in(client, cleaner)
    _available(client, cleaner, "2026-11-10", "synthetic:completion-origin", key="completion-available")
    preview = _preview(client, "2026-11-10", "completion-preview")
    _publish(client, preview, "completion-publish")
    client.app.state.clock = FrozenClock(datetime(2026, 11, 10, 21, tzinfo=UTC))
    with db() as session:
        task_ids = [str(row.task_id) for row in session.scalars(select(Assignment)).all()]
    for index, task_id in enumerate(task_ids):
        _inbound(client, "+15555550401", f"SM-DONE-COMPLETE-{index}", f"DONE {task_id}")
    return cleaner
