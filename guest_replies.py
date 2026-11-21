import datetime as dt
import hashlib
import logging
import os
import urllib.error
import urllib.request
import uuid
import json
import re


def draft(db, issue, message):
    from catalog import ITEMS
    from research import RULES
    text = message['body'].replace('\u2019', "'")
    safety = re.search(RULES[0][1], text, re.I)
    if safety:
        suggested = 'Please prioritize your safety. If there is immediate danger, contact local emergency services and move to a safe location if you can. Please contact the property manager right away.'
    elif issue['resolution_hint']:
        suggested = 'Thank you for the update. Please let us know if you need anything else during your stay.'
    elif issue['category'] == 'Supplies':
        items = [label.lower() for label, pattern, _ in ITEMS.values() if re.search(pattern, text, re.I)]
        subject = items[0] if len(items) == 1 else 'the missing items'
        suggested = "I'm sorry for the inconvenience, and thank you for letting us know about " + subject + ". We're reviewing what is needed and will update you once a replacement and delivery time are confirmed."
    elif issue['category'] == 'Timing':
        suggested = "Thanks for asking. We're checking the turnover schedule and will confirm whether we can accommodate your requested time."
    elif issue['category'] in ('Instructions', 'Other'):
        suggested = "Thanks for reaching out. We're checking the details for your property so we can give you the correct information."
    else:
        suggested = "I'm sorry for the inconvenience, and thank you for letting us know. We're reviewing the issue and will follow up with the next steps."
    fingerprint = hashlib.sha256(json.dumps({'messages': issue['messages'], 'category': issue['category'], 'lane': issue['lane'], 'resolution_hint': issue['resolution_hint'], 'source_messages_fingerprint': issue['source_messages_fingerprint'], 'conversation_id': issue['conversation_id']}, sort_keys=True).encode()).hexdigest()
    saved = db.execute('SELECT * FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?', (issue['id'], message['id'])).fetchone()
    send = db.execute('SELECT id, state, body, reference, error, approved_at, updated_at FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?', (issue['reservation_id'], message['id'])).fetchone()
    return {'send': dict(send) if send else None, 'message_id': message['id'], 'suggested': suggested, 'body': saved['body'] if saved else suggested, 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'draft_source_fingerprint': saved['source_fingerprint'] if saved else fingerprint, 'stale': bool(saved and saved['source_fingerprint'] != fingerprint), 'updated_at': saved['updated_at'] if saved else None, 'practice': issue['lane'] == 'practice', 'safety': bool(safety), 'sending_enabled': bool(os.environ.get('HOSPITABLE_TOKEN')) and issue['lane'] != 'practice' and not issue.get('upcoming') and len(issue['property_ids']) == 1 and bool(issue['conversation_id'] and issue['source_messages_fingerprint'])}


def save(db, data, timestamp):
    from research import analyze
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not snapshot:
        raise ValueError('Refresh Hospitable before preparing a guest reply')
    issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == data.get('issue_id')), None)
    message = next((item for item in issue['evidence'] if item['id'] == data.get('message_id') and item['sender_type'] == 'guest'), None) if issue else None
    if not message:
        raise ValueError('Choose a guest text from this request')
    body = data.get('body')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 3000:
        raise ValueError('Reply drafts must contain between 1 and 3000 characters')
    current = draft(db, issue, message)
    if type(data.get('version')) is not int or data['version'] != current['version']:
        raise ValueError('This draft changed; reload before saving')
    if data.get('source_fingerprint') != current['source_fingerprint']:
        raise ValueError('The conversation changed; review the latest messages before saving')
    db.execute('INSERT INTO guest_reply_drafts VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(issue_id, message_id) DO UPDATE SET body = excluded.body, version = excluded.version, source_fingerprint = excluded.source_fingerprint, updated_at = excluded.updated_at', (issue['id'], message['id'], body.strip(), current['version'] + 1, current['source_fingerprint'], timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'guest_reply_draft_saved', timestamp, json.dumps({'message_id': message['id'], 'version': current['version'] + 1, 'practice': current['practice'], 'sent': False})))
    return {'saved': True, 'sent': False}


def approve(db, data, timestamp):
    from research import analyze
    row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not row:
        raise ValueError('Refresh Hospitable before sending')
    snapshot = json.loads(row['body'])
    age = dt.datetime.fromisoformat(timestamp) - dt.datetime.fromisoformat(snapshot['synced_at'])
    if not dt.timedelta(0) <= age <= dt.timedelta(minutes=10):
        raise ValueError('Refresh Hospitable before sending; the import must be less than 10 minutes old')
    issue = next((item for item in analyze(snapshot)['issues'] if item['id'] == data.get('issue_id')), None)
    message = next((item for item in issue['evidence'] if item['id'] == data.get('message_id') and item['sender_type'] == 'guest'), None) if issue else None
    if not message:
        raise ValueError('Choose a guest text from this request')
    current = draft(db, issue, message)
    if not current['sending_enabled']:
        raise ValueError('Sending requires a configured Hospitable token and a current stay mapped to one property. Refresh the import first.')
    if not current['updated_at'] or type(data.get('version')) is not int or data['version'] != current['version'] or current['stale'] or data.get('source_fingerprint') != current['source_fingerprint'] or data.get('body') != current['body']:
        raise ValueError('Save and review the latest draft before sending')
    if data.get('approved') is not True:
        raise ValueError('Approve this exact reply for the displayed guest conversation')
    existing = db.execute('SELECT * FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?', (issue['reservation_id'], message['id'])).fetchone()
    if existing and existing['state'] not in ('blocked', 'failed'):
        raise ValueError('A send already exists for this guest text. Check its status in Hospitable before replying again.')
    context = {'property_id': issue['property_ids'][0], 'conversation_id': issue['conversation_id'], 'source_messages_fingerprint': issue['source_messages_fingerprint'], 'source_fingerprint': current['source_fingerprint'], 'draft_version': current['version'], 'check_in': issue['check_in'], 'check_out': issue['check_out']}
    identifier = existing['id'] if existing else str(uuid.uuid4())
    db.execute("INSERT INTO guest_reply_sends VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, NULL, ?) ON CONFLICT(reservation_id, message_id) DO UPDATE SET issue_id = excluded.issue_id, body = excluded.body, context = excluded.context, state = 'queued', reference = NULL, error = NULL, approved_at = excluded.approved_at, attempted_at = NULL, updated_at = excluded.updated_at", (identifier, issue['reservation_id'], message['id'], issue['id'], current['body'], json.dumps(context), timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'guest_reply_approved', timestamp, json.dumps({'send_id': identifier, 'message_id': message['id'], 'draft_version': current['version']})))
    return {'id': identifier, 'state': 'queued'}


def deliver_pending(connect, stop):
    from hospitable import get
    with connect() as db:
        for row in db.execute("SELECT id, issue_id FROM guest_reply_sends WHERE state = 'attempting'").fetchall():
            timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
            db.execute("UPDATE guest_reply_sends SET state = 'unknown', error = 'The app restarted during sending. Check Hospitable before sending anything else.', updated_at = ? WHERE id = ?", (timestamp, row['id']))
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_unknown', timestamp, json.dumps({'send_id': row['id'], 'reason': 'restart'})))
    while not stop.wait(2):
        with connect() as db:
            rows = db.execute("SELECT * FROM guest_reply_sends WHERE state = 'queued' ORDER BY approved_at LIMIT 10").fetchall()
        for row in rows:
            if stop.is_set():
                return
            state = 'blocked'
            reference = None
            error = None
            try:
                context = json.loads(row['context'])
                current = dt.datetime.now(dt.timezone.utc)
                if not dt.timedelta(0) <= current - dt.datetime.fromisoformat(row['approved_at']) <= dt.timedelta(minutes=10):
                    raise ValueError('Approval expired. Review and approve the draft again.')
                token = os.environ.get('HOSPITABLE_TOKEN')
                if not token:
                    raise ValueError('Hospitable access is not configured')
                with connect() as db:
                    since = (current - dt.timedelta(minutes=5)).isoformat()
                    recent = db.execute("SELECT COUNT(*) AS count FROM audit WHERE action = 'guest_reply_send_attempted' AND at >= ?", (since,)).fetchone()['count']
                    same = db.execute("SELECT 1 FROM audit WHERE action = 'guest_reply_send_attempted' AND json_extract(details, '$.reservation_id') = ? AND at >= ?", (row['reservation_id'], (current - dt.timedelta(minutes=1)).isoformat())).fetchone()
                if recent >= 40 or same:
                    continue
                reservation = get('reservations/' + row['reservation_id'] + '?include=properties', token)
                check_in = dt.datetime.fromisoformat(reservation['check_in'].replace('Z', '+00:00'))
                check_out = dt.datetime.fromisoformat(reservation['check_out'].replace('Z', '+00:00'))
                if reservation['id'] != row['reservation_id'] or reservation['reservation_status']['current']['category'] != 'accepted' or not check_in <= current < check_out or [item['id'] for item in reservation['properties']] != [context['property_id']] or reservation['conversation_id'] != context['conversation_id'] or reservation['check_in'] != context['check_in'] or reservation['check_out'] != context['check_out']:
                    raise ValueError('Reservation details changed. Refresh Hospitable and review the reply again.')
                messages = get('reservations/' + row['reservation_id'] + '/messages', token)
                fingerprint = hashlib.sha256(json.dumps(sorted(messages, key=lambda message: str(message['id'])), sort_keys=True).encode()).hexdigest()
                if fingerprint != context['source_messages_fingerprint'] or not any(str(message['id']) == row['message_id'] and message['sender_type'] == 'guest' and message['conversation_id'] == context['conversation_id'] for message in messages):
                    raise ValueError('The conversation changed. Refresh Hospitable and review the reply again.')
                current = dt.datetime.now(dt.timezone.utc)
                if not check_in <= current < check_out or current - dt.datetime.fromisoformat(row['approved_at']) > dt.timedelta(minutes=10):
                    raise ValueError('The stay ended or approval expired. Refresh and review before sending.')
                timestamp = current.isoformat()
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    live = db.execute('SELECT state, context FROM guest_reply_sends WHERE id = ?', (row['id'],)).fetchone()
                    saved = db.execute('SELECT * FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?', (row['issue_id'], row['message_id'])).fetchone()
                    if live['state'] != 'queued' or live['context'] != row['context']:
                        continue
                    if not saved or saved['version'] != context['draft_version'] or saved['body'] != row['body'] or saved['source_fingerprint'] != context['source_fingerprint']:
                        raise ValueError('The draft changed after approval. Review and approve again.')
                    db.execute("UPDATE guest_reply_sends SET state = 'attempting', attempted_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, row['id']))
                    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_attempted', timestamp, json.dumps({'send_id': row['id'], 'reservation_id': row['reservation_id']})))
                state = 'unknown'
                request = urllib.request.Request('https://public.api.hospitable.com/v2/reservations/' + row['reservation_id'] + '/messages', data=json.dumps({'body': row['body']}).encode(), method='POST', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'})
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        if response.status != 202:
                            raise ValueError('Hospitable did not confirm API acceptance')
                        result = json.load(response)
                    reference = result.get('data', {}).get('sent_reference_id')
                    if not isinstance(reference, str) or not reference:
                        raise ValueError('Hospitable did not return a send reference')
                    state = 'accepted'
                except urllib.error.HTTPError as failure:
                    if failure.code in (400, 401, 403, 404, 422, 429):
                        state = 'failed'
                        error = 'Hospitable rejected this send (HTTP ' + str(failure.code) + '). Check message-writing permission and the conversation before approving again.'
                    else:
                        error = 'Send outcome is unknown. Check Hospitable before sending again.'
                except Exception:
                    error = 'Send outcome is unknown. Check Hospitable before sending again.'
            except Exception as failure:
                error = str(failure) if isinstance(failure, ValueError) else 'Could not verify the current Hospitable conversation. Refresh and review before approving again.'
            with connect() as db:
                timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE guest_reply_sends SET state = ?, reference = ?, error = ?, updated_at = ? WHERE id = ? AND state IN (\'queued\',\'attempting\') AND context = ?', (state, reference, error, timestamp, row['id'], row['context']))
                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_' + state, timestamp, json.dumps({'send_id': row['id']})))
            logging.info(json.dumps({'event': 'guest_reply_send_' + state, 'send_id': row['id']}))


def deliver(connect, stop):
    while not stop.is_set():
        try:
            deliver_pending(connect, stop)
        except Exception as error:
            logging.error(json.dumps({'event': 'guest_reply_worker_failed', 'error_type': type(error).__name__}))
            stop.wait(15)
