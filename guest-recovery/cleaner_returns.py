import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit

import local_scheduler

KINDS = {'linens': ('Fresh linens and bed remake', 30), 'targeted': ('Targeted cleaning', 45), 'turnover': ('Missed turnover', 120)}
ESCALATE = re.compile(r'\b(mou?ld|bed\s?bugs?|cockroach\w*|roaches|fleas|gas|sewage|feces|faeces|blood|needles?)\b', re.IGNORECASE)


class SchedulerNotFound(ValueError):
    pass


def configuration():
    workspace = local_scheduler.workspace_id()
    if workspace:
        return {'url': local_scheduler.origin(workspace), 'dashboard_url': '', 'configured': True,
                'stay_configured': True, 'workspace_id': workspace, 'expires_at': None, 'internal': True}
    origin = os.environ.get('CLEANER_SCHEDULER_URL', '').rstrip('/')
    token = os.environ.get('CLEANER_SCHEDULER_TOKEN', '')
    parsed = urlsplit(origin)
    valid = parsed.scheme == 'https' and parsed.hostname and not parsed.path and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password and parsed.port in (None, 443)
    dashboard = os.environ.get('CLEANER_SCHEDULER_DASHBOARD_URL', '')
    if dashboard:
        parsed_dashboard = urlsplit(dashboard)
        if parsed_dashboard.scheme != 'https' or not parsed_dashboard.hostname or parsed_dashboard.path or parsed_dashboard.query or parsed_dashboard.fragment or parsed_dashboard.username or parsed_dashboard.password or parsed_dashboard.port not in (None, 443):
            raise ValueError('CLEANER_SCHEDULER_DASHBOARD_URL must be an HTTPS origin')
    workspace = os.environ.get('CLEANER_SCHEDULER_WORKSPACE_ID', '')
    expires = os.environ.get('CLEANER_SCHEDULER_EXPIRES_AT', '')
    available = bool(valid and token and workspace)
    if expires:
        available = available and dt.datetime.fromisoformat(expires) > dt.datetime.now(dt.UTC)
    return {'url': origin if valid else '', 'dashboard_url': dashboard, 'configured': bool(valid and token and not token.startswith('stay_')), 'stay_configured': available, 'workspace_id': workspace, 'expires_at': expires or None}


def prepare_configuration(data):
    from settings import CONFIG_LOCK
    if local_scheduler.workspace_id():
        raise ValueError('This installation uses the internal scheduler.')
    origin = data.get('url')
    token = data.get('token', '')
    if not isinstance(origin, str) or len(origin) > 250 or not isinstance(token, str) or len(token) > 8000 or (token and not re.fullmatch(r'[A-Za-z0-9_.~-]+', token)):
        raise ValueError('Enter the scheduler HTTPS origin and a valid connection token')
    parsed = urlsplit(origin.rstrip('/'))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Use the scheduler API HTTPS origin without a path')
    dashboard = data.get('dashboard_url', '')
    if not isinstance(dashboard, str) or len(dashboard) > 250:
        raise ValueError('Enter the scheduler dashboard HTTPS origin')
    if dashboard:
        parsed_dashboard = urlsplit(dashboard.rstrip('/'))
        if parsed_dashboard.scheme != 'https' or not parsed_dashboard.hostname or parsed_dashboard.path or parsed_dashboard.query or parsed_dashboard.fragment or parsed_dashboard.username or parsed_dashboard.password or parsed_dashboard.port not in (None, 443):
            raise ValueError('Use the scheduler dashboard HTTPS origin without a path')
    with CONFIG_LOCK:
        previous = configuration()
        previous_token = os.environ.get('CLEANER_SCHEDULER_TOKEN', '')
    if not token and previous['url'] != origin.rstrip('/'):
        raise ValueError('Provide a connection token when changing the scheduler API')
    metadata = verify_connection(origin.rstrip('/'), token or previous_token)
    if previous['workspace_id'] and previous['workspace_id'] != metadata['workspace_id']:
        raise ValueError('This token belongs to a different scheduler workspace. Use the account already connected to these requests.')
    values = {'CLEANER_SCHEDULER_URL': origin.rstrip('/'), 'CLEANER_SCHEDULER_DASHBOARD_URL': dashboard.rstrip('/'), 'CLEANER_SCHEDULER_WORKSPACE_ID': metadata['workspace_id'], 'CLEANER_SCHEDULER_EXPIRES_AT': metadata.get('expires_at') or ''}
    if token:
        values['CLEANER_SCHEDULER_TOKEN'] = token
    return values, previous['url'], previous_token


def configure(prepared):
    from settings import CONFIG_LOCK, persist
    values, previous_url, previous_token = prepared
    with CONFIG_LOCK:
        if configuration()['url'] != previous_url or os.environ.get('CLEANER_SCHEDULER_TOKEN', '') != previous_token:
            raise ValueError('The connection changed while verifying. Reload Settings and try again.')
        persist(values)
        return configuration()


def verify_connection(origin, token):
    if not token:
        raise ValueError('Create a Guest Recovery connection token in the scheduler’s account settings, then paste it here.')
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(origin + '/v1/stay-adjustments', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with opener.open(req, timeout=3) as response:
                result = json.loads(response.read(10000))
            if not isinstance(result, dict) or result.get('timezone') != 'America/New_York' or result.get('checkout_cutoff') != '20:00' or result.get('policy_version') != '2026-11-17-v2':
                raise ValueError('The scheduler needs the current 8 p.m. check-in and checkout integration deployed.')
            if not isinstance(result.get('workspace_id'), str):
                raise TypeError('The scheduler returned no workspace identity.')
            result['workspace_id'] = str(uuid.UUID(result['workspace_id']))
            if token.startswith('stay_'):
                if not isinstance(result.get('expires_at'), str):
                    raise TypeError('The scheduler returned no connection expiry.')
                expires = dt.datetime.fromisoformat(result['expires_at'])
                if expires.tzinfo is None or expires <= dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5):
                    raise ValueError('Create a fresh connection token; this one expires too soon.')
                result['expires_at'] = expires.astimezone(dt.UTC).isoformat()
            return result
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in (401, 403):
                raise ValueError('The scheduler rejected this token. Create a new connection token in its account settings.') from None
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Could not verify the scheduler connection (HTTP ' + str(status) + ').') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise ValueError('The scheduler could not be reached. Your saved connection was not changed.') from None
        except (TypeError, KeyError):
            raise ValueError('The scheduler returned incomplete connection details. The connection was not changed.') from None
        time.sleep(0.25 * 2 ** attempt)


def context(db, issue_id, timestamp):
    from research import analyze
    if not isinstance(issue_id, str) or len(issue_id) > 200:
        raise ValueError('Choose a cleaning complaint')
    row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not row:
        raise ValueError('Refresh Hospitable before preparing a cleaner return')
    snapshot = json.loads(row['body'])
    issue = next((item for item in analyze(snapshot, db)['issues'] if item['id'] == issue_id and item['category'] == 'Cleaning'), None)
    if not issue or len(issue['property_ids']) != 1:
        raise ValueError('Choose a cleaning complaint mapped to one property')
    if any(ESCALATE.search(item['body']) for item in issue['evidence']):
        raise ValueError('This complaint needs manager inspection before a routine cleaner return')
    current = dt.datetime.fromisoformat(timestamp)
    age = current - dt.datetime.fromisoformat(snapshot['synced_at'])
    live = issue['lane'] == 'needs_attention' and dt.timedelta(0) <= age <= dt.timedelta(minutes=10)
    return issue, live


def guest_context_fingerprint(issue):
    return hashlib.sha256(json.dumps([message for message in issue['messages'] if message['sender_type'] == 'guest'], sort_keys=True).encode()).hexdigest()


def describe(row):
    result = dict(row)
    result['plan'] = json.loads(result.pop('body'))
    remote = result.pop('remote')
    result['scheduler'] = json.loads(remote) if remote else None
    return result


def change(db, data, timestamp):
    issue, live = context(db, data.get('issue_id'), timestamp)
    kind = data.get('kind')
    scope = data.get('scope')
    duration = data.get('duration_minutes')
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(scope, str) or not scope.strip() or len(scope) > 1000:
        raise ValueError('Choose a cleaning task and describe the areas to fix')
    if ESCALATE.search(scope):
        raise ValueError('Request manager inspection for this issue')
    if type(duration) is not int or not 15 <= duration <= 240:
        raise ValueError('Allow 15 to 240 minutes for the visit')
    times = []
    for key in ('release_at', 'deadline_at'):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Choose the guest-permitted visit window')
        value = dt.datetime.fromisoformat(value)
        if value.tzinfo is None:
            raise ValueError('Visit times must include a timezone')
        times.append(value.astimezone(dt.UTC))
    start, end = times
    current = dt.datetime.fromisoformat(timestamp)
    if not current < start < end <= current + dt.timedelta(hours=48) or end - start < dt.timedelta(minutes=duration):
        raise ValueError('Choose a future window within 48 hours long enough for the visit')
    submit = data.get('submit') is True
    if submit and (not live or not configuration()['configured']):
        raise ValueError('Sending requires a connected scheduler and a fresh, current-stay complaint')
    if submit and data.get('guest_access_confirmed') is not True:
        raise ValueError('Confirm the guest agreed to entry during this window')
    if submit and not dt.datetime.fromisoformat(issue['check_in']) <= start < end <= dt.datetime.fromisoformat(issue['check_out']):
        raise ValueError('The visit must fit within this guest’s stay')
    fingerprint = guest_context_fingerprint(issue)
    if data.get('source_fingerprint') != fingerprint:
        raise ValueError('The complaint changed. Refresh and review it again')
    old = db.execute('SELECT * FROM cleaner_returns WHERE issue_id = ? ORDER BY created_at DESC, id DESC LIMIT 1', (issue['id'],)).fetchone()
    if type(data.get('version')) is not int or data['version'] != (old['version'] if old else 0):
        raise ValueError('This visit changed. Refresh before saving')
    repeat = old and old['state'] in ('completed', 'cancelled') and data.get('new_visit') is True
    if old and old['state'] != 'draft' and not repeat:
        raise ValueError('This request was already sent. Manage its schedule in the scheduling bot')
    identifier = old['id'] if old and not repeat else str(uuid.uuid4())
    plan = {'external_request_id': identifier, 'property_external_id': issue['property_ids'][0], 'reservation_external_id': issue['reservation_id'], 'scope': scope.strip(), 'release_at': start.isoformat(), 'deadline_at': end.isoformat(), 'duration_minutes': duration, 'guest_access_confirmed': data.get('guest_access_confirmed') is True}
    body = {'kind': kind, 'request': plan, 'source_fingerprint': fingerprint, 'scheduler_url': configuration()['url'] if submit else None}
    state = 'queued' if submit else 'draft'
    db.execute('INSERT INTO cleaner_returns (id, issue_id, body, state, version, created_at, updated_at, next_poll) VALUES (?, ?, ?, ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body, state = excluded.state, error = NULL, version = cleaner_returns.version + 1, updated_at = excluded.updated_at, next_poll = excluded.next_poll', (identifier, issue['id'], json.dumps(body), state, timestamp, timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'cleaner_return_queued' if submit else 'cleaner_return_drafted', timestamp, json.dumps({'request_id': identifier, 'kind': kind, 'duration_minutes': duration})))
    return {'id': identifier, 'state': state}


def confirm(db, data, timestamp):
    identifier = data.get('id')
    row = db.execute('SELECT * FROM cleaner_returns WHERE id = ?', (identifier,)).fetchone() if isinstance(identifier, str) else None
    if not row or row['state'] != 'completed' or type(data.get('version')) is not int or data['version'] != row['version']:
        raise ValueError('Refresh and select a completed cleaner visit')
    body = json.loads(row['body'])
    if body.get('guest_confirmed_at'):
        raise ValueError('Guest confirmation was already recorded')
    body['guest_confirmed_at'] = timestamp
    issue, _ = context(db, row['issue_id'], timestamp)
    body['confirmation_fingerprint'] = guest_context_fingerprint(issue)
    db.execute('UPDATE cleaner_returns SET body = ?, version = version + 1, updated_at = ? WHERE id = ?', (json.dumps(body), timestamp, identifier))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'cleaner_return_guest_confirmed', timestamp, json.dumps({'request_id': identifier})))
    return {'id': identifier, 'state': 'completed'}


def decline(db, data, timestamp):
    issue, _ = context(db, data.get('issue_id'), timestamp)
    fingerprint = guest_context_fingerprint(issue)
    if data.get('source_fingerprint') != fingerprint:
        raise ValueError('The conversation changed. Refresh before denying this return')
    old = db.execute('SELECT * FROM cleaner_returns WHERE issue_id = ? ORDER BY created_at DESC, id DESC LIMIT 1', (issue['id'],)).fetchone()
    if type(data.get('version')) is not int or data['version'] != (old['version'] if old else 0):
        raise ValueError('The request changed. Refresh before denying this return')
    if old and old['state'] not in ('draft', 'completed', 'cancelled'):
        raise ValueError('This request has already been sent. Cancel it in the scheduling bot')
    identifier = old['id'] if old and old['state'] == 'draft' else str(uuid.uuid4())
    body = {'manager_declined': True, 'source_fingerprint': fingerprint, 'request': {'scope': 'Cleaner return declined by manager'}}
    db.execute("INSERT INTO cleaner_returns (id, issue_id, body, state, version, created_at, updated_at, next_poll) VALUES (?, ?, ?, 'cancelled', 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body, state = 'cancelled', version = cleaner_returns.version + 1, updated_at = excluded.updated_at", (identifier, issue['id'], json.dumps(body), timestamp, timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'cleaner_return_declined', timestamp, json.dumps({'request_id': identifier})))
    return {'id': identifier, 'state': 'cancelled'}


def retry(db, data, timestamp):
    identifier = data.get('id')
    row = db.execute('SELECT * FROM cleaner_returns WHERE id = ?', (identifier,)).fetchone() if isinstance(identifier, str) else None
    if not row or row['state'] != 'blocked' or type(data.get('version')) is not int or data['version'] != row['version']:
        raise ValueError('Refresh before retrying this request')
    if not configuration()['configured']:
        raise ValueError('Configure the cleaner scheduler connection first')
    db.execute("UPDATE cleaner_returns SET state = 'queued', error = NULL, version = version + 1, next_poll = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, identifier))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'cleaner_return_retry_queued', timestamp, json.dumps({'request_id': identifier})))
    return {'id': identifier, 'state': 'queued'}


def schedule_action(db, path, data, timestamp):
    identifier = data.get('id')
    row = db.execute('SELECT * FROM cleaner_returns WHERE id = ?', (identifier,)).fetchone() if isinstance(identifier, str) else None
    if not row or row['state'] != 'submitted' or type(data.get('version')) is not int or data['version'] != row['version']:
        raise ValueError('Refresh before reviewing this schedule proposal')
    issue, live = context(db, row['issue_id'], timestamp)
    plan = json.loads(row['body'])
    if (plan.get('scheduler_url') or '').startswith('internal-scheduler:'):
        raise ValueError('Manage this touch-up in Cleaner Scheduling.')
    fingerprint = guest_context_fingerprint(issue)
    if not live or data.get('source_fingerprint') != fingerprint:
        raise ValueError('The guest conversation or stay changed. Review entry permission before scheduling')
    if fingerprint != plan['source_fingerprint']:
        from cleaning_proposal import propose
        from fulfillment import profile
        destination = profile(db, issue['property_ids'][0])
        current = propose(issue, destination['address'].get('timezone') or 'America/New_York', timestamp)
        original = plan['request']
        if current['status'] != 'ready' or current['scope'] != original['scope'] or current['duration_minutes'] > original['duration_minutes'] or dt.datetime.fromisoformat(current['release_at']) > dt.datetime.fromisoformat(original['release_at']) or dt.datetime.fromisoformat(current['deadline_at']) < dt.datetime.fromisoformat(original['deadline_at']):
            raise ValueError('The guest’s latest needs or entry permission differ. Review this visit in the scheduler before continuing')
        plan['source_fingerprint'] = fingerprint
    remote = json.loads(row['remote']) if row['remote'] else {}
    if path != '/api/cleaner-returns/notify' and (remote.get('assignment') or remote.get('status') not in ('awaiting_schedule', 'awaiting_approval', 'needs_manager')):
        raise ValueError('This visit is already assigned or is not available for scheduling')
    if plan.get('approval', {}).get('state') in ('queued', 'uncertain'):
        raise ValueError('Schedule approval is already being checked')
    if path == '/api/cleaner-returns/notify':
        if remote.get('status') != 'scheduled' or not remote.get('assignment'):
            raise ValueError('Choose a scheduled visit that is awaiting cleaner acceptance')
        if plan.get('notice_retry', {}).get('state') in ('queued', 'uncertain'):
            raise ValueError('The cleaner notice is already being checked')
        if remote.get('notice', {}).get('state') == 'unknown':
            raise ValueError('Check the existing email outcome before sending another notice')
        plan['notice_generation'] = plan.get('notice_generation', 0) + 1
        plan['notice_retry'] = {'state': 'queued', 'key': str(uuid.uuid5(uuid.NAMESPACE_URL, 'guest-recovery/return-notice/' + identifier + '/' + str(plan['notice_generation'])))}
        action = 'cleaner_return_notice_requested'
    elif path == '/api/cleaner-returns/propose':
        plan['proposal_generation'] = plan.get('proposal_generation', 0) + 1
        plan['refresh_proposal'] = True
        plan.pop('approval', None)
        action = 'cleaner_return_proposal_requested'
    else:
        proposal = remote.get('proposal')
        if not isinstance(proposal, dict):
            raise ValueError('Generate a feasible proposal before approving it')
        expected = {'run_id': proposal['run_id'], 'expected_version': proposal['version'], 'expected_fingerprint': proposal['fingerprint'], 'expected_day_revision': proposal['day_revision']}
        if any(data.get(key) != value for key, value in expected.items()) or type(data.get('expected_version')) is not int or type(data.get('expected_day_revision')) is not int:
            raise ValueError('The displayed schedule proposal changed. Refresh before approving')
        plan['approval'] = {'state': 'queued', 'request': expected, 'key': str(uuid.uuid5(uuid.NAMESPACE_URL, 'guest-recovery/return-approval/' + identifier + '/' + proposal['run_id']))}
        action = 'cleaner_return_schedule_approved'
    db.execute('UPDATE cleaner_returns SET body = ?, error = NULL, version = version + 1, updated_at = ?, next_poll = ? WHERE id = ?', (json.dumps(plan), timestamp, timestamp, identifier))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], action, timestamp, json.dumps({'request_id': identifier, 'run_id': (remote.get('proposal') or {}).get('run_id')})))
    return {'id': identifier, 'state': 'submitted'}


def request(origin, method, path, body=None, key=None):
    workspace = local_scheduler.workspace_id()
    if workspace and origin == local_scheduler.origin(workspace):
        return local_scheduler.request(workspace, method, path, body)
    from settings import CONFIG_LOCK
    with CONFIG_LOCK:
        settings = configuration()
        available = settings['stay_configured'] if path.startswith('/v1/stay-adjustments') else settings['configured']
        if not available or settings['url'] != origin:
            raise ValueError('The scheduler connection changed. Review this request before continuing')
        token = os.environ['CLEANER_SCHEDULER_TOKEN']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    payload = json.dumps(body).encode() if body is not None else None
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        req = urllib.request.Request(settings['url'] + path, data=payload, headers=headers, method=method)
        try:
            with opener.open(req, timeout=400 if path.endswith('/proposals') else 60 if path.startswith('/v1/stay-adjustments') else 10) as response:
                result = json.loads(response.read(100000))
            if not isinstance(result, dict):
                raise TypeError('Scheduler returned an invalid response')
            return result
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if method == 'GET' and status == 404:
                raise SchedulerNotFound('This request has not been found in the scheduler') from None
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Scheduler request failed (HTTP ' + str(status) + '). Review the connection or open the scheduling bot.') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise ValueError('Scheduler is unreachable. The request remains recorded; check the scheduling bot before retrying.') from None
        time.sleep(2 ** attempt)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def synchronize(connect, stop):
    while not stop.wait(5):
        timestamp = dt.datetime.now(dt.UTC).isoformat()
        try:
            with connect() as db:
                rows = db.execute("SELECT * FROM cleaner_returns WHERE state IN ('queued', 'submitted', 'uncertain') AND next_poll <= ? ORDER BY next_poll LIMIT 10", (timestamp,)).fetchall()
        except sqlite3.Error:
            logging.getLogger(__name__).exception(json.dumps({'event': 'cleaner_return_queue_unavailable'}))
            continue
        for row in rows:
            if stop.is_set():
                return
            timestamp = dt.datetime.now(dt.UTC).isoformat()
            attempted = row['attempted_at'] is not None
            plan = json.loads(row['body'])
            try:
                if row['state'] == 'queued':
                    try:
                        result = request(plan['scheduler_url'], 'GET', '/v1/cleaner-returns/' + row['id'])
                    except SchedulerNotFound:
                        with connect() as db:
                            issue, live = context(db, row['issue_id'], timestamp)
                            fingerprint = guest_context_fingerprint(issue)
                            if not live or fingerprint != plan['source_fingerprint']:
                                raise ValueError('Guest complaint or stay changed. Refresh and review entry permission before continuing.') from None
                            db.execute('UPDATE cleaner_returns SET attempted_at = ? WHERE id = ? AND version = ?', (timestamp, row['id'], row['version']))
                        attempted = True
                        result = request(plan['scheduler_url'], 'POST', '/v1/cleaner-returns', plan['request'], row['id'])
                else:
                    result = request(plan['scheduler_url'], 'GET', '/v1/cleaner-returns/' + row['id'])
                approval = plan.get('approval')
                if approval and approval['state'] in ('queued', 'uncertain'):
                    if result.get('status') in ('scheduled', 'accepted', 'completed'):
                        approval['state'] = 'confirmed'
                    elif approval['state'] == 'uncertain':
                        approval['state'] = 'blocked'
                        raise ValueError('Approval outcome is unconfirmed. Review the scheduler before approving again')
                    else:
                        with connect() as db:
                            issue, live = context(db, row['issue_id'], dt.datetime.now(dt.UTC).isoformat())
                            if not live or guest_context_fingerprint(issue) != plan['source_fingerprint']:
                                approval['state'] = 'blocked'
                                raise ValueError('The guest conversation changed. Scheduling approval was stopped')
                        approval['state'] = 'uncertain'
                        result = request(plan['scheduler_url'], 'POST', '/v1/cleaner-returns/' + row['id'] + '/approve', approval['request'], approval['key'])
                        approval['state'] = 'confirmed'
                elif (result.get('status') == 'awaiting_schedule' and not result.get('proposal_failure') or plan.get('refresh_proposal')) and not result.get('assignment'):
                    with connect() as db:
                        issue, live = context(db, row['issue_id'], dt.datetime.now(dt.UTC).isoformat())
                        if not live or guest_context_fingerprint(issue) != plan['source_fingerprint']:
                            raise ValueError('Review the latest guest conversation before generating a schedule proposal')
                    key = str(uuid.uuid5(uuid.NAMESPACE_URL, 'guest-recovery/return-proposal/' + row['id'] + '/' + str(plan.get('proposal_generation', 0))))
                    result = request(plan['scheduler_url'], 'POST', '/v1/cleaner-returns/' + row['id'] + '/proposals', {}, key)
                    plan['refresh_proposal'] = False
                elif plan.get('notice_retry', {}).get('state') in ('queued', 'uncertain'):
                    notice = plan['notice_retry']
                    if notice['state'] == 'uncertain':
                        notice['state'] = 'confirmed' if result.get('notice', {}).get('state') in ('queued', 'sending', 'sent', 'unknown') else 'blocked'
                    else:
                        with connect() as db:
                            issue, live = context(db, row['issue_id'], dt.datetime.now(dt.UTC).isoformat())
                            if not live or guest_context_fingerprint(issue) != plan['source_fingerprint']:
                                notice['state'] = 'blocked'
                                raise ValueError('Review the guest’s latest entry permission before notifying the cleaner')
                        notice['state'] = 'uncertain'
                        result = request(plan['scheduler_url'], 'POST', '/v1/cleaner-returns/' + row['id'] + '/notify', {}, notice['key'])
                        notice['state'] = 'confirmed'
                if result.get('status') not in ('awaiting_schedule', 'awaiting_approval', 'scheduled', 'accepted', 'completed', 'cancelled', 'needs_manager') or not isinstance(result.get('task_id'), str):
                    raise ValueError('Scheduler response did not contain a valid visit status')
                state = 'completed' if result['status'] == 'completed' else 'cancelled' if result['status'] == 'cancelled' else 'submitted'
                error = None
            except Exception as failure:
                logging.getLogger(__name__).exception(json.dumps({'event': 'cleaner_return_sync_failed', 'request_id': row['id'], 'error_type': type(failure).__name__}))
                result = json.loads(row['remote']) if row['remote'] else None
                if row['state'] == 'queued':
                    state = 'uncertain' if attempted else 'draft'
                elif row['state'] == 'uncertain' and isinstance(failure, SchedulerNotFound):
                    state = 'blocked'
                else:
                    state = row['state']
                error = str(failure) if isinstance(failure, ValueError) else 'Could not synchronize this cleaner return'
            updated = dt.datetime.now(dt.UTC)
            try:
                with connect() as db:
                    saved = db.execute('UPDATE cleaner_returns SET state = ?, body = ?, remote = ?, error = ?, version = version + 1, updated_at = ?, next_poll = ? WHERE id = ? AND version = ?', (state, json.dumps(plan), json.dumps(result) if result else None, error, updated.isoformat(), (updated + dt.timedelta(seconds=60)).isoformat(), row['id'], row['version']))
                    if saved.rowcount and (row['state'] != state or (json.loads(row['remote']) if row['remote'] else None) != result):
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'cleaner_return_synchronized', updated.isoformat(), json.dumps({'request_id': row['id'], 'state': state, 'scheduler_status': result.get('status') if result else None})))
            except sqlite3.Error:
                logging.getLogger(__name__).exception(json.dumps({'event': 'cleaner_return_status_save_failed', 'request_id': row['id']}))
