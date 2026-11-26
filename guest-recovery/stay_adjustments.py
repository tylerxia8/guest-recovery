import datetime as dt
import hashlib
import json
import logging
import sqlite3

import local_scheduler
from cleaner_returns import SchedulerNotFound, configuration
from cleaner_returns import request as scheduler_request
from reply_library import question_parts
from stay_policy import EASTERN, POLICY_VERSION, policy_reply, superseding_message
from stay_policy import request as policy_request


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS stay_policy_activation (id INTEGER PRIMARY KEY CHECK(id = 1), activated_at TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO stay_policy_activation VALUES (1, ?)', (dt.datetime.now(dt.UTC).isoformat(),))
    db.execute("CREATE TABLE IF NOT EXISTS stay_requests (id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, message_id TEXT NOT NULL, kind TEXT NOT NULL, plan TEXT NOT NULL, payload TEXT, source_fingerprint TEXT NOT NULL, scheduler_url TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','pending','approved','denied','uncertain','invalidated')), remote TEXT, error TEXT, checked_at TEXT, next_poll TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    if 'approved_remote' not in {row[1] for row in db.execute('PRAGMA table_info(stay_requests)')}:
        db.execute('ALTER TABLE stay_requests ADD COLUMN approved_remote TEXT')
        db.execute("UPDATE stay_requests SET approved_remote = remote WHERE state = 'approved' AND json_extract(remote, '$.decision') = 'approved'")
    db.execute('CREATE INDEX IF NOT EXISTS stay_requests_poll ON stay_requests(next_poll)')
    db.execute('CREATE INDEX IF NOT EXISTS stay_requests_reservation ON stay_requests(reservation_id)')


def source_fingerprint(issue, message):
    source = {key: issue[key] for key in ('reservation_id', 'property_ids', 'check_in', 'check_out')}
    source['message'] = {key: message[key] for key in ('id', 'body', 'created_at')}
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def assessments(db, issue, message, timestamp):
    result = []
    issue = {**issue, **effective_stay(db, issue)}
    for part in question_parts(message['body']):
        proposal = policy_request(issue, {**message, 'body': part}, timestamp)
        if not proposal:
            continue
        row = db.execute('SELECT * FROM stay_requests WHERE id = ?', (proposal['request_id'],)).fetchone()
        remote = None
        error = None
        if row and row['source_fingerprint'] == source_fingerprint(issue, message):
            frozen = json.loads(row['plan'])
            if row['payload']:
                proposal['target_at'] = frozen['target_at']
            remote = json.loads(row['remote']) if row['remote'] else None
            error = row['error']
            if remote and row['checked_at'] and issue.get('lane') != 'practice' and dt.datetime.fromisoformat(timestamp) - dt.datetime.fromisoformat(row['checked_at']) > dt.timedelta(minutes=2):
                remote = None
                error = 'Refresh the cleaning schedule before confirming this change.'
        if superseding_message(issue, message, proposal['kind'], timestamp) or row and row['state'] == 'invalidated':
            remote = None
            error = 'The guest changed or withdrew this request. Review their latest message; existing confirmed times remain protected.'
            proposal.update({'decision': 'pending', 'reason': 'guest_request_changed', 'can_request_scheduler': False})
        decision = remote['decision'] if remote else 'approved' if proposal['reason'] == 'already_covered' else proposal['decision']
        result.append({**proposal, 'part': part, 'decision': decision, 'body': policy_reply(proposal, remote), 'reason': remote.get('reason') if remote else proposal['reason'], 'scheduler_approved': bool(remote and remote['decision'] == 'approved'), 'scheduler_checked_at': row['checked_at'] if row else None, 'scheduler_version': remote.get('version') if remote else None, 'error': error, 'remote': remote})
    return result


def invalidate_changed_request(db, row, conversation, message, timestamp):
    changed = superseding_message(conversation, message, row['kind'], timestamp)
    if not changed:
        return False
    updated = db.execute("UPDATE stay_requests SET state = 'invalidated', next_poll = NULL, updated_at = ?, error = ? WHERE id = ? AND state != 'invalidated'", (timestamp, 'The guest changed or withdrew this request. Review the latest message; any existing scheduler reservation remains protected.', row['id'])).rowcount
    if updated:
        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['reservation_id'] + ':instructions', 'stay_request_superseded', timestamp, json.dumps({'request_id': row['id'], 'latest_message_id': changed})))
    return True


def current_request(db, row, timestamp):
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    data = json.loads(snapshot['body']) if snapshot else None
    conversation = next((item for item in data['conversations'] if item['reservation_id'] == row['reservation_id']), None) if data else None
    message = next((item for item in conversation['messages'] if item['id'] == row['message_id']), None) if conversation else None
    accepted = set(data.get('active_reservation_ids', [])) | set(data.get('upcoming_reservation_ids', [])) if data else set()
    if row['reservation_id'] not in accepted or not message or source_fingerprint(conversation, message) != row['source_fingerprint'] or not dt.timedelta(0) <= dt.datetime.fromisoformat(timestamp) - dt.datetime.fromisoformat(data['synced_at']) <= dt.timedelta(minutes=10):
        raise ValueError('Refresh Hospitable and review the current guest request before scheduling.')
    return conversation, message


def effective_stay(db, conversation):
    start = dt.datetime.fromisoformat(conversation['check_in'])
    end = dt.datetime.fromisoformat(conversation['check_out'])
    identifiers = []
    for row in db.execute("SELECT id, plan, approved_remote FROM stay_requests WHERE reservation_id = ? AND approved_remote IS NOT NULL", (conversation['reservation_id'],)):
        plan = json.loads(row['plan'])
        if plan['property_ids'] != conversation.get('property_ids') or dt.datetime.fromisoformat(plan['check_in']) != dt.datetime.fromisoformat(conversation['check_in']) or dt.datetime.fromisoformat(plan['check_out']) != dt.datetime.fromisoformat(conversation['check_out']):
            continue
        remote = json.loads(row['approved_remote'])
        target = dt.datetime.fromisoformat(remote.get('target_at') or plan['target_at'])
        if plan['kind'] == 'early_checkin':
            target = max(target, dt.datetime.fromisoformat(remote['effective_checkin_at']))
            start = min(start, target)
        else:
            end = max(end, min(target, dt.datetime.fromisoformat(remote['effective_checkout_at'])))
        identifiers.append(row['id'])
    return {'effective_check_in': start.isoformat(), 'effective_check_out': end.isoformat(), 'stay_adjustment_ids': identifiers}


def refresh_snapshot(db):
    row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not row:
        return
    snapshot = json.loads(row['body'])
    accepted = set(snapshot.get('active_reservation_ids', [])) | set(snapshot.get('upcoming_reservation_ids', []))
    current = dt.datetime.now(dt.UTC)
    active, upcoming = [], []
    for conversation in snapshot['conversations']:
        conversation.update(effective_stay(db, conversation))
        if conversation['reservation_id'] not in accepted:
            continue
        start = dt.datetime.fromisoformat(conversation['effective_check_in'])
        end = dt.datetime.fromisoformat(conversation['effective_check_out'])
        if start <= current < end:
            active.append(conversation['reservation_id'])
        elif current < start <= current + dt.timedelta(days=2):
            upcoming.append(conversation['reservation_id'])
    snapshot['active_reservation_ids'] = active
    snapshot['upcoming_reservation_ids'] = upcoming
    snapshot['active_reservation_count'] = len(active)
    db.execute('UPDATE research_snapshot SET body = ? WHERE id = 1', (json.dumps(snapshot),))


def ingest(db, snapshot, timestamp):
    current = dt.datetime.fromisoformat(timestamp)
    if not dt.timedelta(0) <= current - dt.datetime.fromisoformat(snapshot['synced_at']) <= dt.timedelta(minutes=10):
        return
    activated = dt.datetime.fromisoformat(db.execute('SELECT activated_at FROM stay_policy_activation WHERE id = 1').fetchone()[0])
    accepted = set(snapshot.get('active_reservation_ids', [])) | set(snapshot.get('upcoming_reservation_ids', []))
    settings = configuration()
    for conversation in snapshot['conversations']:
        if conversation['reservation_id'] not in accepted or len(conversation.get('property_ids', [])) != 1 or not conversation.get('conversation_id'):
            continue
        for row in db.execute("SELECT * FROM stay_requests WHERE reservation_id = ? AND state IN ('queued','pending','approved','uncertain')", (conversation['reservation_id'],)).fetchall():
            original = next((item for item in conversation['messages'] if item['id'] == row['message_id']), None)
            if original:
                invalidate_changed_request(db, row, conversation, original, timestamp)
        for message in conversation['messages']:
            if message['sender_type'] != 'guest' or not activated <= dt.datetime.fromisoformat(message['created_at']) <= current:
                continue
            for proposal in assessments(db, conversation, message, timestamp):
                if proposal['reason'] in ('policy_information', 'guest_request_changed'):
                    continue
                existing = db.execute('SELECT payload, source_fingerprint, state FROM stay_requests WHERE id = ?', (proposal['request_id'],)).fetchone()
                if existing and (existing['payload'] or existing['source_fingerprint'] != source_fingerprint(conversation, message) or not proposal['can_request_scheduler']):
                    continue
                payload = None
                if proposal['can_request_scheduler']:
                    payload = {'request_id': proposal['request_id'], 'provider': 'hospitable', 'property_external_id': conversation['property_ids'][0], 'reservation_external_id': conversation['reservation_id'], 'kind': proposal['kind'], 'requested_at': proposal['requested_at'], 'target_at': proposal['target_at']}
                plan = {key: value for key, value in proposal.items() if key not in ('remote', 'error')}
                plan.update({key: conversation[key] for key in ('check_in', 'check_out', 'property_ids')})
                state = proposal['decision'] if proposal['decision'] in ('denied', 'approved') else 'queued' if payload else 'pending'
                if existing:
                    db.execute('DELETE FROM stay_requests WHERE id = ? AND payload IS NULL', (proposal['request_id'],))
                db.execute('INSERT INTO stay_requests (id, reservation_id, message_id, kind, plan, payload, source_fingerprint, scheduler_url, state, remote, error, checked_at, next_poll, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)', (proposal['request_id'], conversation['reservation_id'], message['id'], proposal['kind'], json.dumps(plan), json.dumps(payload) if payload else None, source_fingerprint(conversation, message), settings['url'], state, timestamp if payload else None, timestamp, timestamp))
                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (conversation['reservation_id'] + ':instructions', 'stay_request_recorded', timestamp, json.dumps({'request_id': proposal['request_id'], 'kind': proposal['kind'], 'state': state, 'policy_version': POLICY_VERSION})))


def validate_remote(result, row):
    if result.get('request_id') != row['id'] or result.get('decision') not in ('approved', 'denied', 'pending'):
        raise ValueError('The scheduler returned a different or invalid stay request.')
    for key in ('effective_checkin_at', 'effective_checkout_at'):
        value = dt.datetime.fromisoformat(result[key])
        if value.tzinfo is None:
            raise ValueError('The scheduler returned a stay time without a timezone.')
    if dt.datetime.fromisoformat(result['effective_checkin_at']) >= dt.datetime.fromisoformat(result['effective_checkout_at']):
        raise ValueError('The scheduler returned an invalid stay interval.')
    plan = json.loads(row['plan'])
    start = dt.datetime.fromisoformat(result['effective_checkin_at']).astimezone(EASTERN)
    end = dt.datetime.fromisoformat(result['effective_checkout_at']).astimezone(EASTERN)
    booked_start = dt.datetime.fromisoformat(plan['check_in']).astimezone(EASTERN)
    booked_end = dt.datetime.fromisoformat(plan['check_out']).astimezone(EASTERN)
    internal = (row['scheduler_url'] or '').startswith('internal-scheduler:')
    maximum = dt.datetime.combine(booked_end.date(), dt.time.max, EASTERN) if internal else max(booked_end, dt.datetime.combine(booked_end.date(), dt.time(12), EASTERN))
    if start.date() != booked_start.date() or start > booked_start or end.date() != booked_end.date() or end < booked_end or end > maximum:
        raise ValueError('The scheduler returned times outside this booking and stay policy.')
    if result['decision'] == 'approved':
        proposal = json.loads(row['plan'])
        approved_target = dt.datetime.fromisoformat(result['target_at'])
        requested_target = dt.datetime.fromisoformat(proposal['target_at'])
        if approved_target.tzinfo is None or proposal['kind'] == 'late_checkout' and approved_target != requested_target or proposal['kind'] == 'early_checkin' and not requested_target <= approved_target <= booked_start:
            raise ValueError('The scheduler approval differs from this guest’s requested change.')
        policy_reply(proposal, result)
    return result


def synchronize(connect, stop):
    while not stop.wait(5):
        timestamp = dt.datetime.now(dt.UTC).isoformat()
        try:
            with connect() as db:
                rows = db.execute("SELECT * FROM stay_requests WHERE payload IS NOT NULL AND next_poll <= ? AND state IN ('queued','pending','approved','uncertain') ORDER BY next_poll LIMIT 10", (timestamp,)).fetchall()
            for row in rows:
                if stop.is_set():
                    return
                plan = json.loads(row['plan'])
                final_time = max(dt.datetime.fromisoformat(plan['check_out']), dt.datetime.fromisoformat(plan['target_at']))
                if dt.datetime.fromisoformat(timestamp) >= final_time:
                    with connect() as db:
                        db.execute('UPDATE stay_requests SET next_poll = NULL WHERE id = ?', (row['id'],))
                    continue
                settings = configuration()
                try:
                    origin = local_scheduler.resolve_origin(row['scheduler_url'], settings['url'])
                    with connect() as db:
                        conversation, message = current_request(db, row, timestamp)
                        if invalidate_changed_request(db, row, conversation, message, timestamp):
                            continue
                        db.execute('UPDATE stay_requests SET scheduler_url = ? WHERE id = ? AND scheduler_url = ?', (origin, row['id'], row['scheduler_url']))
                    if not settings['stay_configured']:
                        raise ValueError('Connect the cleaning scheduler before confirming this change.')
                    try:
                        remote = None if settings.get('internal') else scheduler_request(origin, 'GET', '/v1/stay-adjustments/' + row['id'])
                    except SchedulerNotFound:
                        remote = None
                    if remote is None or remote.get('decision') == 'pending':
                        payload = json.loads(row['payload'])
                        if settings.get('internal'):
                            payload.update(booked_check_in=plan['check_in'], booked_check_out=plan['check_out'])
                        remote = scheduler_request(origin, 'POST', '/v1/stay-adjustments', payload, row['id'])
                    result = validate_remote(remote, {**dict(row), "scheduler_url": origin})
                except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
                    with connect() as db:
                        db.execute('UPDATE stay_requests SET error = ?, next_poll = ?, updated_at = ? WHERE id = ?', (str(error) if isinstance(error, ValueError) else 'Could not verify the cleaning schedule. No change is confirmed.', (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)).isoformat(), timestamp, row['id']))
                    logging.getLogger(__name__).warning(json.dumps({'event': 'stay_request_unconfirmed', 'request_id': row['id'], 'error_type': type(error).__name__}))
                    continue
                with connect() as db:
                    checked = dt.datetime.now(dt.UTC).isoformat()
                    try:
                        conversation, message = current_request(db, row, checked)
                    except ValueError:
                        continue
                    if invalidate_changed_request(db, row, conversation, message, checked):
                        continue
                    updated = db.execute("UPDATE stay_requests SET state = ?, remote = ?, approved_remote = CASE WHEN ? = 'approved' THEN ? ELSE approved_remote END, error = NULL, checked_at = ?, updated_at = ?, next_poll = ? WHERE id = ? AND source_fingerprint = ? AND state != 'invalidated'", (result['decision'], json.dumps(result), result['decision'], json.dumps(result), checked, checked, (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)).isoformat() if result['decision'] != 'denied' else None, row['id'], row['source_fingerprint'])).rowcount
                    if not updated:
                        continue
                    refresh_snapshot(db)
                    if row['state'] != result['decision']:
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['reservation_id'] + ':instructions', 'stay_request_' + result['decision'], checked, json.dumps({'request_id': row['id'], 'kind': row['kind'], 'reason': result.get('reason')})))
                if result['decision'] == 'approved' and settings.get('internal'):
                    try:
                        from guest_replies import queue_stay_reply
                        with connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            saved = db.execute('SELECT * FROM stay_requests WHERE id = ?', (row['id'],)).fetchone()
                            if saved:
                                queue_stay_reply(db, saved, checked)
                    except Exception as error:
                        logging.getLogger(__name__).warning(json.dumps({
                            'event': 'automatic_stay_reply_not_queued', 'error_type': type(error).__name__,
                        }))

        except sqlite3.Error as error:
            logging.getLogger(__name__).error(json.dumps({'event': 'stay_request_worker_failed', 'error_type': type(error).__name__}))


def verify_for_reply(connect, identifiers):
    for identifier in identifiers:
        with connect() as db:
            row = db.execute('SELECT * FROM stay_requests WHERE id = ?', (identifier,)).fetchone()
            if not row:
                raise ValueError('The stay request is unavailable.')
            conversation, message = current_request(db, row, dt.datetime.now(dt.UTC).isoformat())
            changed = invalidate_changed_request(db, row, conversation, message, dt.datetime.now(dt.UTC).isoformat())
        if not row or not row['scheduler_url']:
            raise ValueError('The stay change is not connected to the scheduler.')
        if changed or row['state'] == 'invalidated' or not message:
            raise ValueError('Review the guest’s latest request before confirming a stay change.')
        origin = local_scheduler.resolve_origin(row['scheduler_url'], configuration()['url'])
        if origin.startswith('internal-scheduler:'):
            plan = json.loads(row['plan'])
            payload = json.loads(row['payload'])
            payload.update(booked_check_in=plan['check_in'], booked_check_out=plan['check_out'])
            response = scheduler_request(origin, 'POST', '/v1/stay-adjustments', payload, identifier)
        else:
            response = scheduler_request(origin, 'GET', '/v1/stay-adjustments/' + identifier)
        remote = validate_remote(response, {**dict(row), "scheduler_url": origin})
        previous = json.loads(row['remote']) if row['remote'] else None
        with connect() as db:
            checked = dt.datetime.now(dt.UTC).isoformat()
            conversation, message = current_request(db, row, checked)
            changed = invalidate_changed_request(db, row, conversation, message, checked)
            updated = 0 if changed else db.execute("UPDATE stay_requests SET state = ?, remote = ?, approved_remote = CASE WHEN ? = 'approved' THEN ? ELSE approved_remote END, checked_at = ?, error = NULL, updated_at = ? WHERE id = ? AND source_fingerprint = ? AND state != 'invalidated'", (remote['decision'], json.dumps(remote), remote['decision'], json.dumps(remote), checked, checked, identifier, row['source_fingerprint'])).rowcount
            refresh_snapshot(db)
        if not updated:
            raise ValueError('The guest request changed during schedule verification. Review the latest conversation.')
        if not previous or any(previous.get(key) != remote.get(key) for key in ('decision', 'target_at', 'effective_checkin_at', 'effective_checkout_at')):
            raise ValueError('The stay decision or time changed. Refresh the schedule and guest reply.')
