import uuid

from app.models import Assignment, Cleaner, CleanerAccessLink
from app.services.sms_lifecycle import drain_mock_sms, enqueue_schedule_messages
from sqlalchemy import select
from tests.completed_fixture import complete_fixture, db
from tests.test_scheduling import _approve, _available, _cleaner, _preview, _publish, _sync, _turnover
from tests.test_setup import headers
from tests.test_sms import _opt_in


def prepare(client, *, two=False):
    _approve(client)
    stays = _turnover("remove-A", "P-A", "2026-11-11")
    if two:
        stays += _turnover("remove-B", "P-B", "2026-11-11")
    _sync(client, stays, "remove-sync")
    cleaner = _cleaner(
        client, name="Example departing cleaner", phone="+15555550711", properties=["P-A", "P-B"], key="remove-cleaner"
    )
    _available(client, cleaner, "2026-11-11", "synthetic:remove-origin", key="remove-availability")
    _publish(client, _preview(client, "2026-11-11", "remove-preview"), "remove-publish")
    return cleaner


def remove(client, cleaner, key="remove", viewer=False, version=None):
    if version is None:
        with db() as session:
            version = session.get(Cleaner, uuid.UUID(cleaner)).version
    return client.post(
        f"/v1/cleaners/{cleaner}/remove", headers=headers(key, viewer), json={"expected_version": version}
    )


def test_removal_revokes_access_releases_work_and_invalidates_preview(client):
    cleaner = prepare(client)
    _opt_in(client, cleaner)
    clock, settings = client.app.state.clock, client.app.state.settings
    with db() as session:
        from datetime import timedelta

        from app.services.sms_lifecycle import issue_link

        token = issue_link(
            session, clock=clock, settings=settings, cleaner_id=uuid.UUID(cleaner), ttl=timedelta(hours=2)
        )
        enqueue_schedule_messages(session, clock=clock, settings=settings)
        version = session.get(Cleaner, uuid.UUID(cleaner)).version
        session.commit()
    stale = _preview(client, "2026-11-11", "before-removal")
    assert remove(client, cleaner, viewer=True, version=version).status_code == 403
    result = remove(client, cleaner, version=version)
    assert result.status_code == 200, result.text
    assert result.json() == {"active": False, "released": 1, "needs_review": 0}
    assert remove(client, cleaner, version=version).json() == result.json()
    assert remove(client, cleaner, key="stale-removal", version=version).status_code == 409
    assert cleaner not in {row["id"] for row in client.get("/v1/cleaners", headers=headers()).json()["items"]}
    assert client.get(f"/v1/cleaner-links/{token}").status_code == 404
    with db() as session:
        assert not session.get(Cleaner, uuid.UUID(cleaner)).active
        assert all(row.revoked_at for row in session.scalars(select(CleanerAccessLink)))
        assert all(row.status == "superseded" for row in session.scalars(select(Assignment)))
        assert drain_mock_sms(session, clock=clock, settings=settings)["gateway_calls"] == 0
        session.commit()
    response = client.post(
        f"/v1/schedules/runs/{stale['id']}/publish",
        headers={**headers("stale-plan"), "If-Match": '"1"'},
        json={
            "expected_fingerprint": stale["input_fingerprint"],
            "expected_day_revision": stale["day_revision"],
        },
    )
    assert response.status_code == 409
    assert not _preview(client, "2026-11-11", "without-cleaner")["candidate_plan"]["placed"]
    replacement = _cleaner(
        client, name="Example replacement", phone="+15555550712", properties=["P-A"], key="replacement"
    )
    _available(client, replacement, "2026-11-11", "synthetic:replacement", key="replacement-hours")
    fresh = _preview(client, "2026-11-11", "replacement-plan")
    assert fresh["candidate_plan"]["placed"][0]["cleaner_id"] == replacement
    _publish(client, fresh, "replacement-publish")


def test_removal_preserves_locked_assignment_and_flags_it(client):
    cleaner = prepare(client, two=True)
    with db() as session:
        rows = list(session.scalars(select(Assignment).order_by(Assignment.id)))
        rows[0].is_locked = True
        locked_id, start, owner = rows[0].id, rows[0].planned_start, rows[0].cleaner_id
        session.commit()
    result = remove(client, cleaner)
    assert result.status_code == 200, result.text
    assert result.json()["needs_review"] == 1
    assert result.json()["released"] == 1
    with db() as session:
        locked = session.get(Assignment, locked_id)
        assert locked.is_locked and locked.status == "blocked"
        assert (locked.planned_start, locked.cleaner_id) == (start, owner)


def test_removal_preserves_completed_work(client):
    complete_fixture(client)
    with db() as session:
        assignment = session.scalar(select(Assignment))
        cleaner = str(assignment.cleaner_id)
        before = (assignment.status, assignment.completed_at, assignment.version)
        assignment_id = assignment.id
    assert remove(client, cleaner).status_code == 200
    with db() as session:
        assignment = session.get(Assignment, assignment_id)
        assert (assignment.status, assignment.completed_at, assignment.version) == before


def test_removal_flags_possibly_started_work_without_reassigning_it(client):
    from datetime import timedelta

    from app.clock import FrozenClock

    cleaner = prepare(client)
    with db() as session:
        assignment = session.scalar(select(Assignment))
        assignment_id, start, owner = assignment.id, assignment.planned_start, assignment.cleaner_id
    client.app.state.clock = FrozenClock(start + timedelta(minutes=1))
    result = remove(client, cleaner)
    assert result.json()["needs_review"] == 1
    assert result.json()["released"] == 0
    with db() as session:
        assignment = session.get(Assignment, assignment_id)
        assert assignment.status == "blocked"
        assert (assignment.planned_start, assignment.cleaner_id) == (start, owner)
