import datetime as dt
import json
import logging
import re
import uuid

from purchases import email_settings
from mailer import email_ready, send_email

SUPPORTED = ('laundry_pods', 'trash_bags', 'dish_pods', 'toilet_paper', 'soap', 'shampoo')


def profile(db):
    row = db.execute('SELECT body, version FROM local_supplier WHERE id = 1').fetchone()
    return json.loads(row['body']) | {'version': row['version']} if row else {'email': '', 'property_ids': [], 'item_types': list(SUPPORTED), 'version': 0}


def describe(row):
    request = dict(row)
    request['details'] = json.loads(request.pop('body'))
    request.pop('payload')
    request.pop('email_id')
    current = dt.datetime.now(dt.timezone.utc)
    details = request['details']
    request['fallback'] = False
    if row['state'] == 'cancelled':
        request['status'] = 'Reply deadline passed without confirmation — use Amazon' if details.get('timed_out') else 'Cancelled — use Amazon'
        request['fallback'] = True
    elif row['state'] == 'unavailable':
        request['status'] = 'Cleaner cannot supply in time — use Amazon'
        request['fallback'] = True
    elif row['state'] == 'available':
        if current >= dt.datetime.fromisoformat(details['arrival']):
            request['status'] = 'Cleaner arrival estimate has passed — reconfirm or use Amazon'
            request['fallback'] = True
        else:
            request['status'] = 'Cleaner reported available; delivery is not dispatched'
    elif row['notice_status'] == 'failed':
        request['status'] = 'Cleaner email send is unconfirmed — use Amazon' if (row['error'] or '').startswith('Gmail send') else 'Cleaner email failed — use Amazon'
        request['fallback'] = True
    elif current >= dt.datetime.fromisoformat(details['reply_by']):
        request['status'] = 'No confirmed reply before the deadline — use Amazon'
        request['fallback'] = True
    elif row['notice_status'] == 'pending':
        request['status'] = 'Cleaner has not been emailed yet; Amazon remains available'
    else:
        request['status'] = 'Waiting for cleaner availability; Amazon remains available'
    return request


def change(db, path, data, timestamp):
    if path == '/api/local-supplier':
        email = data.get('email')
        properties = data.get('property_ids')
        items = data.get('item_types')
        version = data.get('version')
        if not isinstance(email, str) or len(email) > 254 or (email and not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', email)):
            raise ValueError('Enter a plain cleaner email address')
        if not isinstance(properties, list) or any(not isinstance(value, str) for value in properties) or len(properties) != len(set(properties)):
            raise ValueError('Choose rental units from the imported list')
        if not isinstance(items, list) or any(value not in SUPPORTED for value in items) or len(items) != len(set(items)):
            raise ValueError('Choose supported cleaning supplies or toiletries')
        for identifier in properties:
            if not db.execute('SELECT id FROM rental_units WHERE id = ?', (identifier,)).fetchone():
                raise ValueError('Unknown rental unit')
        if type(version) is not int or profile(db)['version'] != version:
            raise ValueError('Cleaner settings changed; reload before saving')
        body = {'email': email, 'property_ids': properties, 'item_types': items}
        db.execute('INSERT INTO local_supplier VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body, version = excluded.version', (json.dumps(body), version + 1))
        identifier = 'hiddenbrook-cleaner'
        action = 'local_supplier_updated'
    elif path == '/api/local-requests':
        from research import analyze
        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        if not snapshot:
            raise ValueError('Research snapshot is missing')
        issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == data.get('issue_id') and item['category'] == 'Supplies'), None)
        if not issue or len(issue['property_ids']) != 1:
            raise ValueError('Confirm exactly one rental unit before contacting the cleaner')
        supplier = profile(db)
        property_id = issue['property_ids'][0]
        items = [item for item in issue['item_types'] if item in supplier['item_types']]
        if property_id not in supplier['property_ids'] or not supplier['email'] or not items:
            raise ValueError('Confirm cleaner email, unit coverage, and supported items first')
        needed = data.get('needed_by')
        minutes = data.get('reply_minutes')
        if not isinstance(needed, str) or type(minutes) is not int or minutes not in (5, 10, 15, 30):
            raise ValueError('Choose a needed-by time and reply deadline')
        needed = dt.datetime.fromisoformat(needed)
        current = dt.datetime.fromisoformat(timestamp)
        if needed.tzinfo is None or not current < needed <= current + dt.timedelta(days=2):
            raise ValueError('Needed-by time must be in the next 48 hours and include a timezone')
        if db.execute("SELECT id FROM local_requests WHERE issue_id = ? AND state IN ('pending', 'available')", (issue['id'],)).fetchone():
            raise ValueError('Cancel the existing cleaner check before requesting another')
        identifier = str(uuid.uuid4())
        body = {'property_id': property_id, 'property': issue['property'], 'email': supplier['email'], 'item_types': items, 'needed_by': needed.astimezone(dt.timezone.utc).isoformat(), 'reply_by': min(current + dt.timedelta(minutes=minutes), needed).astimezone(dt.timezone.utc).isoformat()}
        db.execute("INSERT INTO local_requests VALUES (?, ?, ?, 'pending', 'pending', NULL, NULL, NULL, 1, ?, ?)", (identifier, issue['id'], json.dumps(body), timestamp, timestamp))
        action = 'cleaner_availability_requested'
    else:
        match = re.fullmatch(r'/api/local-requests/([a-f0-9-]{36})/(available|unavailable|cancel)', path)
        if not match:
            raise ValueError('Unknown cleaner request action')
        identifier, action = match.groups()
        row = db.execute('SELECT * FROM local_requests WHERE id = ?', (identifier,)).fetchone()
        if not row or type(data.get('version')) is not int or row['version'] != data['version'] or row['state'] not in ('pending', 'available'):
            raise ValueError('Cleaner request changed; reload before updating')
        details = json.loads(row['body'])
        if action == 'available':
            if row['notice_status'] != 'sent' or dt.datetime.fromisoformat(timestamp) >= dt.datetime.fromisoformat(details['reply_by']):
                raise ValueError('No current emailed request is awaiting a reply; use Amazon or create a new check')
            arrival = data.get('arrival')
            if not isinstance(arrival, str):
                raise ValueError('Record the cleaner’s confirmed arrival time')
            arrival = dt.datetime.fromisoformat(arrival)
            if arrival.tzinfo is None or not dt.datetime.fromisoformat(timestamp) < arrival <= dt.datetime.fromisoformat(details['needed_by']):
                raise ValueError('Arrival must be in the future and no later than the needed-by time; otherwise record unavailable')
            if data.get('items_confirmed') is not True:
                raise ValueError('Confirm that the cleaner has all requested items')
            details['arrival'] = arrival.astimezone(dt.timezone.utc).isoformat()
        state = {'available': 'available', 'unavailable': 'unavailable', 'cancel': 'cancelled'}[action]
        db.execute('UPDATE local_requests SET state = ?, body = ?, version = version + 1, updated_at = ? WHERE id = ?', (state, json.dumps(details), timestamp, identifier))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, action, timestamp, json.dumps({'simulation': True})))
    return {'id': identifier}


def deliver_requests(connect, stop):
    from catalog import ITEMS
    while not stop.is_set():
        settings = email_settings()
        if email_ready(settings):
            with connect() as db:
                rows = db.execute("SELECT * FROM local_requests WHERE state = 'pending' AND notice_status = 'pending' ORDER BY created_at LIMIT 10").fetchall()
            for row in rows:
                if stop.is_set():
                    return
                try:
                    details = json.loads(row['body'])
                    if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(details['reply_by']):
                        details['timed_out'] = True
                        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
                        with connect() as db:
                            cursor = db.execute("UPDATE local_requests SET state = 'cancelled', body = ?, version = version + 1, updated_at = ? WHERE id = ? AND state = 'pending' AND notice_status = 'pending'", (json.dumps(details), timestamp, row['id']))
                            if cursor.rowcount:
                                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'cleaner_check_timed_out', timestamp, '{}'))
                        continue
                    with connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = db.execute('SELECT * FROM local_requests WHERE id = ?', (row['id'],)).fetchone()
                        if current['state'] != 'pending' or current['notice_status'] != 'pending':
                            continue
                        payload = json.loads(current['payload']) if current['payload'] else None
                        if payload is None:
                            text = '\n'.join(('[SIMULATION: availability inquiry only. Do not dispatch or purchase anything.]', 'Request ID: ' + row['id'], 'Unit: ' + details['property'], 'Items to check: ' + ', '.join(ITEMS[item][0] for item in details['item_types']), 'Needed by: ' + details['needed_by'], 'Please reply by: ' + details['reply_by'], 'Please confirm whether you have all these items, your earliest arrival time, and any charge. If you cannot help in time, please say so. No availability is assumed.', 'Reply to the property manager. This app does not read email replies automatically.'))
                            payload = {'from': settings['NOTIFICATION_FROM'], 'to': [details['email']], 'reply_to': settings['MANAGER_EMAIL'], 'subject': '[SIMULATION] Hiddenbrook supply availability request', 'text': text}
                            db.execute('UPDATE local_requests SET payload = ? WHERE id = ?', (json.dumps(payload), row['id']))
                        if settings['EMAIL_PROVIDER'] == 'gmail':
                            db.execute("UPDATE local_requests SET notice_status = 'failed', error = 'Gmail send is in progress or unconfirmed; do not resend automatically' WHERE id = ?", (row['id'],))
                    result = send_email(settings, payload, 'cleaner-availability/' + row['id'])
                    email_id = result['id']
                    if not email_id.startswith('smtp:'):
                        email_id = str(uuid.UUID(email_id))
                    with connect() as db:
                        db.execute("UPDATE local_requests SET notice_status = 'sent', email_id = ?, error = NULL WHERE id = ?", (email_id, row['id']))
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'cleaner_email_sent', dt.datetime.now(dt.timezone.utc).isoformat(), '{}'))
                except Exception as error:
                    logging.error(json.dumps({'event': 'cleaner_email_failed', 'request_id': row['id'], 'error_type': type(error).__name__}))
                    message = 'Gmail send outcome is unconfirmed. The email may have arrived; check before resending. Use Amazon if the cleaner cannot confirm in time.' if settings['EMAIL_PROVIDER'] == 'gmail' else 'Email failed; use Amazon or cancel and retry the availability check'
                    with connect() as db:
                        db.execute("UPDATE local_requests SET notice_status = 'failed', error = ? WHERE id = ?", (message, row['id']))
                if stop.wait(0.6):
                    return
        stop.wait(15)
