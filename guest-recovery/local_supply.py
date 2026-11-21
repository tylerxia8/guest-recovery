import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from mailer import email_ready, send_email
from purchases import email_settings

SUPPORTED = ('laundry_pods', 'trash_bags', 'dish_pods', 'toilet_paper', 'paper_towels', 'dish_soap', 'sponges', 'rinse_aid', 'soap', 'shampoo', 'conditioner', 'body_wash', 'bath_soap')


class LiveRequestUnavailable(ValueError):
    pass


def live_context(db, issue, snapshot, timestamp):
    from fulfillment import profile as fulfillment_profile
    current = dt.datetime.fromisoformat(timestamp)
    age = current - dt.datetime.fromisoformat(snapshot['synced_at'])
    if not dt.timedelta(0) <= age <= dt.timedelta(minutes=10):
        raise LiveRequestUnavailable('Refresh Hospitable before sending a real inquiry; the import must be less than 10 minutes old.')
    if not issue or issue['category'] != 'Supplies' or issue['lane'] != 'needs_attention' or issue['resolution_hint'] or len(issue['property_ids']) != 1:
        raise LiveRequestUnavailable('Real inquiries require a current, unresolved supply request mapped to one unit.')
    unit = db.execute('SELECT name FROM rental_units WHERE id = ?', (issue['property_ids'][0],)).fetchone()
    if not unit or not unit['name'].startswith('HB'):
        raise LiveRequestUnavailable('Real cleaner inquiries are limited to HB units.')
    destination = fulfillment_profile(db, issue['property_ids'][0])
    supplier = profile(db)
    if not destination['verified']:
        raise LiveRequestUnavailable('This unit is missing a usable delivery address in Hospitable.')
    if destination['property_id'] not in supplier['property_ids'] or not supplier['email'] or not any(item in supplier['item_types'] for item in issue['item_types']):
        raise LiveRequestUnavailable('Confirm cleaner coverage, email, and supported items first.')
    if not email_ready(email_settings()):
        raise LiveRequestUnavailable('Configure email sending before using real inquiries.')
    if not current < dt.datetime.fromisoformat(issue['check_out']):
        raise LiveRequestUnavailable('This stay has ended.')
    return destination, supplier


def recheck_live(db, details, issue_id, timestamp):
    from research import analyze
    row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not row:
        raise LiveRequestUnavailable('Hospitable import is missing.')
    snapshot = json.loads(row['body'])
    issue = next((item for item in analyze(snapshot)['issues'] if item['id'] == issue_id), None)
    destination, supplier = live_context(db, issue, snapshot, timestamp)
    fingerprint = hashlib.sha256(json.dumps(issue['evidence'], sort_keys=True).encode()).hexdigest()
    items = [item for item in issue['item_types'] if item in supplier['item_types']]
    if destination['property_id'] != details['property_id'] or issue['property'] != details['property'] or destination['version'] != details['profile_version'] or supplier['version'] != details['supplier_version'] or supplier['email'] != details['email'] or items != details['item_types'] or fingerprint != details['evidence_fingerprint']:
        raise LiveRequestUnavailable('The unit profile, cleaner settings, or complaint changed. Review and create a new inquiry.')
    if dt.datetime.fromisoformat(details['needed_by']) > dt.datetime.fromisoformat(issue['check_out']):
        raise LiveRequestUnavailable('The needed-by time is after checkout.')


def profile(db):
    row = db.execute('SELECT body, version FROM local_supplier WHERE id = 1').fetchone()
    return json.loads(row['body']) | {'version': row['version']} if row else {'email': '', 'property_ids': [], 'item_types': list(SUPPORTED), 'version': 0}


def describe(row):
    request = dict(row)
    request['details'] = json.loads(request.pop('body'))
    request.pop('payload')
    request.pop('email_id')
    current = dt.datetime.now(dt.UTC)
    details = request['details']
    request['fallback'] = False
    if details.get('received_at'):
        request['status'] = 'Manager confirmed all cleaner-supplied items received'
    elif row['state'] == 'cancelled':
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
    simulation = True
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
        snapshot = json.loads(snapshot['body'])
        issue = next((item for item in analyze(snapshot)['issues'] if item['id'] == data.get('issue_id') and item['category'] == 'Supplies'), None)
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
        mode = data.get('mode', 'simulation')
        if mode not in ('simulation', 'live'):
            raise ValueError('Choose simulation or live inquiry')
        if mode == 'live':
            destination, supplier = live_context(db, issue, snapshot, timestamp)
            fingerprint = hashlib.sha256(json.dumps(issue['evidence'], sort_keys=True).encode()).hexdigest()
            if type(data.get('profile_version')) is not int or type(data.get('supplier_version')) is not int or data['profile_version'] != destination['version'] or data['supplier_version'] != supplier['version'] or data.get('evidence_fingerprint') != fingerprint:
                raise ValueError('The displayed request or settings changed. Refresh and confirm the listed items again.')
            if data.get('needed_confirmed') is not True:
                raise ValueError('Confirm these listed items are still needed and authorize the real availability email.')
            if needed > dt.datetime.fromisoformat(issue['check_out']):
                raise ValueError('Needed-by time must be before checkout')
        if db.execute("SELECT id FROM local_requests WHERE issue_id = ? AND state IN ('pending', 'available')", (issue['id'],)).fetchone():
            raise ValueError('Cancel the existing cleaner check before requesting another')
        identifier = str(uuid.uuid4())
        body = {'property_id': property_id, 'property': issue['property'], 'email': supplier['email'], 'item_types': items, 'needed_by': needed.astimezone(dt.UTC).isoformat(), 'reply_by': min(current + dt.timedelta(minutes=minutes), needed).astimezone(dt.UTC).isoformat()}
        body['mode'] = mode
        simulation = mode != 'live'
        if mode == 'live':
            from fulfillment import quantities
            body['supply_quantities'] = [item for item in quantities(issue, destination) if item['item_type'] in items]
            body.update({'profile_version': destination['version'], 'supplier_version': supplier['version'], 'evidence_fingerprint': fingerprint, 'timezone': destination['address']['timezone']})
        db.execute("INSERT INTO local_requests VALUES (?, ?, ?, 'pending', 'pending', NULL, NULL, NULL, 1, ?, ?)", (identifier, issue['id'], json.dumps(body), timestamp, timestamp))
        action = 'cleaner_availability_requested'
    else:
        match = re.fullmatch(r'/api/local-requests/([a-f0-9-]{36})/(available|unavailable|cancel|received)', path)
        if not match:
            raise ValueError('Unknown cleaner request action')
        identifier, action = match.groups()
        row = db.execute('SELECT * FROM local_requests WHERE id = ?', (identifier,)).fetchone()
        if not row or type(data.get('version')) is not int or row['version'] != data['version'] or row['state'] not in ('pending', 'available'):
            raise ValueError('Cleaner request changed; reload before updating')
        details = json.loads(row['body'])
        simulation = details.get('mode') != 'live'
        if details.get('received_at'):
            raise ValueError('Receipt was already confirmed')
        if action == 'received':
            if row['state'] != 'available' or data.get('items_received') is not True:
                raise ValueError('Confirm all requested items were actually received after cleaner availability was recorded')
            details['received_at'] = timestamp
        if action == 'available':
            if details.get('mode') == 'live':
                recheck_live(db, details, row['issue_id'], timestamp)
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
            details['arrival'] = arrival.astimezone(dt.UTC).isoformat()
        state = {'available': 'available', 'unavailable': 'unavailable', 'cancel': 'cancelled', 'received': 'received'}[action]
        db.execute('UPDATE local_requests SET state = ?, body = ?, version = version + 1, updated_at = ? WHERE id = ?', (state, json.dumps(details), timestamp, identifier))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, action, timestamp, json.dumps({'simulation': simulation})))
    return {'id': identifier}


def deliver_requests(connect, stop):
    from catalog import ITEMS
    while not stop.is_set():
        timestamp = dt.datetime.now(dt.UTC).isoformat()
        try:
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                expired = db.execute("SELECT * FROM local_requests WHERE state = 'pending' AND json_extract(body, '$.reply_by') <= ? ORDER BY created_at LIMIT 100", (timestamp,)).fetchall()
                for row in expired:
                    details = json.loads(row['body'])
                    details['timed_out'] = True
                    db.execute("UPDATE local_requests SET state = 'cancelled', body = ?, version = version + 1, updated_at = ? WHERE id = ?", (json.dumps(details), timestamp, row['id']))
                    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'cleaner_check_timed_out', timestamp, '{}'))
        except sqlite3.Error as error:
            logging.getLogger(__name__).error(json.dumps({'event': 'cleaner_deadline_update_failed', 'error_type': type(error).__name__}))
            if stop.wait(15):
                return
            continue
        settings = email_settings()
        if email_ready(settings):
            with connect() as db:
                rows = db.execute("SELECT * FROM local_requests WHERE state = 'pending' AND notice_status = 'pending' ORDER BY created_at LIMIT 10").fetchall()
            for row in rows:
                if stop.is_set():
                    return
                try:
                    details = json.loads(row['body'])
                    with connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        current = db.execute('SELECT * FROM local_requests WHERE id = ?', (row['id'],)).fetchone()
                        if current['state'] != 'pending' or current['notice_status'] != 'pending' or dt.datetime.now(dt.UTC) >= dt.datetime.fromisoformat(details['reply_by']):
                            continue
                        if details.get('mode') == 'live':
                            recheck_live(db, details, row['issue_id'], dt.datetime.now(dt.UTC).isoformat())
                        payload = json.loads(current['payload']) if current['payload'] else None
                        if payload is None:
                            timezone = details.get('timezone', 'America/New_York')
                            needed = dt.datetime.fromisoformat(details['needed_by']).astimezone(ZoneInfo(timezone)).strftime('%b %d, %Y at %I:%M %p %Z')
                            reply = dt.datetime.fromisoformat(details['reply_by']).astimezone(ZoneInfo(timezone)).strftime('%b %d, %Y at %I:%M %p %Z')
                            heading = 'Availability inquiry only. Do not dispatch or purchase anything.' if details.get('mode') == 'live' else '[SIMULATION: availability inquiry only. Do not dispatch or purchase anything.]'
                            text = '\n'.join((heading, 'Request ID: ' + row['id'], 'Unit: ' + details['property'], 'Items to check: ' + (', '.join(ITEMS[item['item_type']][0] + ': ' + str(item['quantity']) + ' ' + item['unit'] for item in details['supply_quantities']) if details.get('supply_quantities') else ', '.join(ITEMS[item][0] for item in details['item_types'])), 'Needed by: ' + needed, 'Please reply by: ' + reply, 'Time zone: ' + timezone, 'Please confirm whether you have all these items, your earliest arrival time, and any charge. If you cannot help in time, please say so. No availability is assumed.', 'Reply to the property manager. This app does not read email replies automatically.'))
                            if details.get('mode') == 'live':
                                from settings import public_origin
                                origin = public_origin()
                                if origin:
                                    token = secrets.token_urlsafe(32)
                                    db.execute('INSERT INTO cleaner_response_links VALUES (?, ?, NULL)', (row['id'], hashlib.sha256(token.encode()).hexdigest()))
                                    text += '\nRespond securely: ' + origin + '/cleaner-response#' + token
                                    text += '\nThis link is private to this request and expires at the reply deadline. Availability only; wait for the manager before dispatching.'
                            subject = 'Hiddenbrook supply availability request' if details.get('mode') == 'live' else '[SIMULATION] Hiddenbrook supply availability request'
                            payload = {'from': settings['NOTIFICATION_FROM'], 'to': [details['email']], 'reply_to': settings['MANAGER_EMAIL'], 'subject': subject, 'text': text}
                            db.execute('UPDATE local_requests SET payload = ? WHERE id = ?', (json.dumps(payload), row['id']))
                        if settings['EMAIL_PROVIDER'] == 'gmail':
                            db.execute("UPDATE local_requests SET notice_status = 'failed', error = 'Gmail send is in progress or unconfirmed; do not resend automatically' WHERE id = ?", (row['id'],))
                    result = send_email(settings, payload, 'cleaner-availability/' + row['id'])
                    email_id = result['id']
                    if not email_id.startswith('smtp:'):
                        email_id = str(uuid.UUID(email_id))
                    with connect() as db:
                        db.execute("UPDATE local_requests SET notice_status = 'sent', email_id = ?, error = NULL WHERE id = ?", (email_id, row['id']))
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'cleaner_email_sent', dt.datetime.now(dt.UTC).isoformat(), '{}'))
                except LiveRequestUnavailable as error:
                    with connect() as db:
                        db.execute("UPDATE local_requests SET state = 'cancelled', error = ?, version = version + 1, updated_at = ? WHERE id = ? AND state = 'pending'", (str(error), dt.datetime.now(dt.UTC).isoformat(), row['id']))
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'live_cleaner_inquiry_blocked', dt.datetime.now(dt.UTC).isoformat(), '{}'))
                except Exception as error:
                    logging.getLogger(__name__).error(json.dumps({'event': 'cleaner_email_failed', 'request_id': row['id'], 'error_type': type(error).__name__}), exc_info=(RuntimeError, RuntimeError(type(error).__name__), error.__traceback__))
                    message = 'Gmail send outcome is unconfirmed. The email may have arrived; check before resending. Use Amazon if the cleaner cannot confirm in time.' if settings['EMAIL_PROVIDER'] == 'gmail' else 'Email failed; use Amazon or cancel and retry the availability check'
                    with connect() as db:
                        db.execute("UPDATE local_requests SET notice_status = 'failed', error = ? WHERE id = ?", (message, row['id']))
                if stop.wait(0.6):
                    return
        stop.wait(15)
