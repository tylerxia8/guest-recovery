import datetime as dt
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
import uuid


def draft(db, issue, message):
    from catalog import ITEMS
    from safety import POLICY_VERSION, detect, guest_message
    text = message['body'].replace('\u2019', "'")
    safety = detect(text)
    instruction_versions = []
    instruction_context = None
    stay_policy_approvals = []
    needs_manager = False
    if safety:
        from fulfillment import profile
        destination = profile(db, issue['property_ids'][0]) if len(issue['property_ids']) == 1 else None
        city = (destination or {}).get('address', {}).get('city') or 'Charlotte'
        suggested = guest_message(safety, city)
    elif issue['resolution_hint']:
        suggested = 'Thank you for the update. Please let us know if you need anything else during your stay.'
    elif issue['category'] == 'Supplies':
        items = [label.lower() for label, pattern, _ in ITEMS.values() if re.search(pattern, text, re.IGNORECASE)]
        subject = items[0] if len(items) == 1 else 'the missing items'
        suggested = "I'm sorry for the inconvenience, and thank you for letting us know about " + subject + ". We're reviewing what is needed and will update you once a replacement and delivery time are confirmed."
    elif issue['category'] == 'Instructions':
        from property_instructions import solution
        answer = solution(db, issue, message)
        suggested = answer['body']
        instruction_versions = answer['guide_versions']
        stay_policy_approvals = [item['request_id'] for item in answer.get('stay_requests', []) if item.get('scheduler_approved')]
        instruction_context = hashlib.sha256(json.dumps({'body': answer['body'], 'missing': answer['missing'], 'versions': instruction_versions}, sort_keys=True).encode()).hexdigest()
        needs_manager = bool(answer['missing']) or not bool(suggested)
    else:
        suggested = "I'm sorry for the inconvenience, and thank you for letting us know. We're reviewing the issue and will follow up with the next steps."
    fingerprint_data = {'messages': issue['messages'], 'category': issue['category'], 'lane': issue['lane'], 'resolution_hint': issue['resolution_hint'], 'source_messages_fingerprint': issue['source_messages_fingerprint'], 'conversation_id': issue['conversation_id']}
    fingerprint_data.update({key: issue[key] for key in ('property_ids', 'reservation_id', 'check_in', 'check_out')})
    fingerprint_data.update({key: issue.get(key) for key in ('effective_check_in', 'effective_check_out', 'stay_adjustment_ids', 'stay_phase')})
    if safety:
        fingerprint_data['safety_policy'] = POLICY_VERSION
        fingerprint_data['safety_message'] = suggested
    if instruction_versions:
        fingerprint_data['instruction_versions'] = instruction_versions
    if instruction_context:
        fingerprint_data['instruction_context'] = instruction_context
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    saved = db.execute('SELECT * FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?', (issue['id'], message['id'])).fetchone()
    if saved and saved['source_fingerprint'] == fingerprint:
        needs_manager = False
    send = db.execute('SELECT id, state, body, reference, error, approved_at, updated_at FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?', (issue['reservation_id'], message['id'])).fetchone()
    return {'send': dict(send) if send else None, 'message_id': message['id'], 'suggested': suggested, 'needs_manager': needs_manager, 'instruction_context': instruction_context, 'instruction_versions': instruction_versions, 'stay_policy_approvals': stay_policy_approvals, 'body': saved['body'] if saved else suggested, 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'draft_source_fingerprint': saved['source_fingerprint'] if saved else fingerprint, 'stale': bool(saved and saved['source_fingerprint'] != fingerprint), 'updated_at': saved['updated_at'] if saved else None, 'practice': issue['lane'] == 'practice', 'safety': bool(safety), 'safety_kind': safety, 'sending_enabled': bool(os.environ.get('HOSPITABLE_TOKEN')) and issue['lane'] != 'practice' and len(issue['property_ids']) == 1 and bool(issue['conversation_id'] and issue['source_messages_fingerprint'])}


def save(db, data, timestamp):
    from research import analyze
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not snapshot:
        raise ValueError('Refresh Hospitable before preparing a guest reply')
    issue = next((item for item in analyze(json.loads(snapshot['body']), db)['issues'] if item['id'] == data.get('issue_id')), None)
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
    issue = next((item for item in analyze(snapshot, db)['issues'] if item['id'] == data.get('issue_id')), None)
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
    return _queue_reply(db, issue, message, current, timestamp, existing)


def _queue_reply(db, issue, message, current, timestamp, existing=None, *, automatic_stay=False, maintenance_question=None, automatic_supply=False):
    context = {'property_id': issue['property_ids'][0], 'conversation_id': issue['conversation_id'], 'source_messages_fingerprint': issue['source_messages_fingerprint'], 'source_fingerprint': current['source_fingerprint'], 'draft_version': current['version'], 'check_in': issue['check_in'], 'check_out': issue['check_out']}
    context['instruction_versions'] = current['instruction_versions']
    context['instruction_context'] = current['instruction_context']
    context['stay_adjustment_ids'] = list(dict.fromkeys([*issue.get('stay_adjustment_ids', []), *current['stay_policy_approvals']]))
    context['prearrival_reply'] = issue.get('stay_phase') == 'upcoming' and issue['category'] == 'Instructions'
    if automatic_stay:
        context['automatic_stay'] = True
    if automatic_supply:
        context['automatic_supply'] = True
    if maintenance_question:
        context['maintenance_question'] = maintenance_question
    identifier = existing['id'] if existing else str(uuid.uuid4())
    db.execute("INSERT INTO guest_reply_sends VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, NULL, ?) ON CONFLICT(reservation_id, message_id) DO UPDATE SET issue_id = excluded.issue_id, body = excluded.body, context = excluded.context, state = 'queued', reference = NULL, error = NULL, approved_at = excluded.approved_at, attempted_at = NULL, updated_at = excluded.updated_at", (identifier, issue['reservation_id'], message['id'], issue['id'], current['body'], json.dumps(context), timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'maintenance_question_queued' if maintenance_question else 'stay_reply_queued_automatically' if automatic_stay else 'supply_acknowledgment_queued' if automatic_supply else 'guest_reply_approved', timestamp, json.dumps({'send_id': identifier, 'message_id': message['id'], 'draft_version': current['version']})))
    return {'id': identifier, 'state': 'queued'}


SUPPLY_ACKNOWLEDGMENT = "Thanks for letting us know. We're working on your request and will share an update once we have confirmed details."


def queue_supply_acknowledgment(db, issue, message, timestamp):
    current = draft(db, issue, message)
    if (issue['category'] != 'Supplies' or issue['lane'] != 'needs_attention'
            or not current['sending_enabled'] or current['safety'] or current['updated_at'] or current['send']):
        return False
    reported_at = dt.datetime.fromisoformat(message['created_at'])
    if any(item['sender_type'] != 'guest' and dt.datetime.fromisoformat(item['created_at']) >= reported_at
           for item in issue['messages']):
        return False
    # A mixed emergency report is handled by the safety workflow.
    from safety import detect
    if any(detect(item['body']) for item in issue['evidence'] if item['sender_type'] == 'guest'):
        return False
    _queue_reply(db, issue, message, {**current, 'body': SUPPLY_ACKNOWLEDGMENT}, timestamp, automatic_supply=True)
    return True


def queue_stay_reply(db, request_row, timestamp):
    from research import analyze
    from stay_policy import policy_reply

    if not request_row['scheduler_url'].startswith('internal-scheduler:') or request_row['state'] != 'approved':
        return
    if db.execute('SELECT 1 FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?',
                  (request_row['reservation_id'], request_row['message_id'])).fetchone():
        return
    snapshot_row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not snapshot_row:
        return
    snapshot = json.loads(snapshot_row['body'])
    if not dt.timedelta(0) <= dt.datetime.fromisoformat(timestamp) - dt.datetime.fromisoformat(snapshot['synced_at']) <= dt.timedelta(minutes=10):
        return
    issue_id = request_row['reservation_id'] + ':instructions'
    issue = next((item for item in analyze(snapshot, db)['issues'] if item['id'] == issue_id), None)
    message = next((item for item in issue['evidence'] if item['id'] == request_row['message_id']), None) if issue else None
    if not message or any(item['created_at'] > message['created_at'] for item in issue['messages']):
        return
    prepared = draft(db, issue, message)
    approved_body = policy_reply(json.loads(request_row['plan']), json.loads(request_row['remote']))
    if (not prepared['sending_enabled'] or prepared['send'] or prepared['updated_at']
            or prepared['safety'] or prepared['needs_manager']
            or prepared['stay_policy_approvals'] != [request_row['id']]
            or prepared['suggested'] != approved_body):
        return
    _queue_reply(db, issue, message, prepared, timestamp, automatic_stay=True)


def queue_maintenance_question(db, issue, message, question, timestamp):
    from safety import detect

    if (not os.environ.get('HOSPITABLE_TOKEN') or not question or not issue['source_messages_fingerprint']
            or issue['lane'] != 'needs_attention' or any(detect(item['body']) for item in issue['evidence'])):
        return
    if any(item['created_at'] > message['created_at'] for item in issue['messages']):
        return
    if db.execute('SELECT 1 FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?',
                  (issue['reservation_id'], message['id'])).fetchone():
        return
    if db.execute('SELECT 1 FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?',
                  (issue['id'], message['id'])).fetchone():
        return
    current = {'body': question['body'], 'version': 0, 'source_fingerprint': issue['source_messages_fingerprint'],
               'instruction_versions': [], 'instruction_context': None, 'stay_policy_approvals': []}
    _queue_reply(db, issue, message, current, timestamp, maintenance_question=question)


def emergency_reply_exists(messages, reported_at):
    reported = dt.datetime.fromisoformat(reported_at)
    for message in messages:
        if message['sender_type'] != 'host' or message.get('source') not in ('platform', 'hospitable') or dt.datetime.fromisoformat(message['created_at']) <= reported:
            continue
        text = (message.get('body') or '').replace('\u2019', "'")
        for sentence in re.findall(r'[^.!?\n]+[.!?]?', text):
            if sentence.rstrip().endswith('?') or re.search(r"\b(?:do not|don.t|no need to|not necessary to|avoid)\s+(?:call|dial|contact)\b", sentence, re.IGNORECASE):
                continue
            if re.search(r'\b(?:call|dial|contact)\b.{0,40}\b(?:911|9-1-1|800[ .-]752[ .-]7504|emergency services|fire department)\b', sentence, re.IGNORECASE):
                return True
    return False


def automatic_safety_replies_enabled():
    return os.environ.get('AUTOMATIC_SAFETY_REPLIES', '1') == '1'


def queue_safety_replies(db, alerts, snapshot, timestamp):
    if not automatic_safety_replies_enabled():
        return
    from fulfillment import profile
    from safety import POLICY_VERSION, detect, guest_message
    conversations = {item['reservation_id']: item for item in snapshot['conversations']}
    current = dt.datetime.fromisoformat(timestamp)
    for alert in alerts:
        conversation = conversations[alert['reservation_id']]
        message = next((item for item in conversation['messages'] if item['id'] == alert['message_id']), None)
        if not message:
            continue
        if not dt.timedelta(0) <= current - dt.datetime.fromisoformat(message['created_at']) <= dt.timedelta(minutes=10):
            continue
        if emergency_reply_exists(conversation['messages'], message['created_at']):
            continue
        if db.execute('SELECT 1 FROM guest_reply_sends WHERE reservation_id = ? AND message_id = ?', (alert['reservation_id'], alert['message_id'])).fetchone():
            continue
        kind = detect(message['body'])
        if not kind:
            raise ValueError('Safety alert has no matching hazard')
        property_id = conversation['property_ids'][0]
        destination = profile(db, property_id)
        city = conversation.get('city') or (destination or {}).get('address', {}).get('city') or 'Charlotte'
        context = {'property_id': property_id, 'conversation_id': conversation['conversation_id'], 'source_messages_fingerprint': conversation['source_messages_fingerprint'], 'check_in': conversation['check_in'], 'check_out': conversation['check_out'], 'automatic_safety': kind, 'safety_policy': POLICY_VERSION, 'safety_city': city, 'safety_message_hash': hashlib.sha256(message['body'].encode()).hexdigest()}
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, 'guest-recovery:safety:' + alert['reservation_id'] + ':' + alert['message_id']))
        issue_id = alert['reservation_id'] + ':safety'
        db.execute("INSERT INTO guest_reply_sends VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, NULL, ?)", (identifier, alert['reservation_id'], alert['message_id'], issue_id, guest_message(kind, city), json.dumps(context), timestamp, timestamp))
        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue_id, 'safety_guest_reply_queued', timestamp, json.dumps({'send_id': identifier, 'kind': kind, 'automatic': True})))


def deliver_pending(connect, stop, queue='standard'):
    from hospitable import get
    if queue not in ('standard', 'safety'):
        raise ValueError('Unknown guest-message queue')
    with connect() as db:
        for row in db.execute("SELECT id, issue_id FROM guest_reply_sends WHERE state = 'attempting' AND CASE WHEN json_extract(context, '$.automatic_safety') IS NULL THEN 'standard' ELSE 'safety' END = ?", (queue,)).fetchall():
            timestamp = dt.datetime.now(dt.UTC).isoformat()
            db.execute("UPDATE guest_reply_sends SET state = 'unknown', error = 'The app restarted during sending. Check Hospitable before sending anything else.', updated_at = ? WHERE id = ?", (timestamp, row['id']))
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_unknown', timestamp, json.dumps({'send_id': row['id'], 'reason': 'restart'})))
    while not stop.wait(2):
        with connect() as db:
            rows = db.execute("SELECT * FROM guest_reply_sends WHERE state = 'queued' AND CASE WHEN json_extract(context, '$.automatic_safety') IS NULL THEN 'standard' ELSE 'safety' END = ? ORDER BY approved_at LIMIT 10", (queue,)).fetchall()
        for row in rows:
            if stop.is_set():
                return
            state = 'blocked'
            reference = None
            error = None
            try:
                context = json.loads(row['context'])
                current = dt.datetime.now(dt.UTC)
                if not dt.timedelta(0) <= current - dt.datetime.fromisoformat(row['approved_at']) <= dt.timedelta(minutes=10):
                    raise ValueError('Approval expired. Review and approve the draft again.')
                token = os.environ.get('HOSPITABLE_TOKEN')
                if not token:
                    raise ValueError('Hospitable access is not configured')
                with connect() as db:
                    since = (current - dt.timedelta(minutes=5)).isoformat()
                    recent = db.execute("SELECT COUNT(*) AS count FROM audit WHERE action = 'guest_reply_send_attempted' AND at >= ?", (since,)).fetchone()['count']
                    same = db.execute("SELECT 1 FROM audit WHERE action = 'guest_reply_send_attempted' AND json_extract(details, '$.reservation_id') = ? AND at >= ?", (row['reservation_id'], (current - dt.timedelta(minutes=1)).isoformat())).fetchone()
                if context.get('automatic_safety'):
                    if not automatic_safety_replies_enabled():
                        raise ValueError('Automatic guest safety replies are disabled.')
                    with connect() as db:
                        recent = db.execute("SELECT COUNT(*) AS count FROM audit WHERE action = 'guest_reply_send_attempted' AND json_extract(details, '$.automatic_safety') IS NOT NULL AND at >= ?", (since,)).fetchone()['count']
                        same = db.execute("SELECT 1 FROM audit WHERE action = 'guest_reply_send_attempted' AND json_extract(details, '$.automatic_safety') IS NOT NULL AND json_extract(details, '$.reservation_id') = ? AND at >= ?", (row['reservation_id'], (current - dt.timedelta(minutes=1)).isoformat())).fetchone()
                if recent >= 40 or same:
                    continue
                reservation = get('reservations/' + row['reservation_id'] + '?include=properties', token)
                from stay_adjustments import effective_stay, verify_for_reply
                with connect() as db:
                    bounds = effective_stay(db, {'reservation_id': row['reservation_id'], 'property_ids': [item['id'] for item in reservation['properties']], 'check_in': reservation['check_in'], 'check_out': reservation['check_out']})
                check_in = dt.datetime.fromisoformat(bounds['effective_check_in'])
                check_out = dt.datetime.fromisoformat(bounds['effective_check_out'])
                prearrival = bool(context.get('prearrival_reply') or context.get('automatic_safety')) and current < check_in <= current + dt.timedelta(days=2)
                if reservation['id'] != row['reservation_id'] or reservation['reservation_status']['current']['category'] != 'accepted' or not (check_in <= current < check_out or prearrival) or [item['id'] for item in reservation['properties']] != [context['property_id']] or reservation['conversation_id'] != context['conversation_id'] or reservation['check_in'] != context['check_in'] or reservation['check_out'] != context['check_out']:
                    raise ValueError('Reservation details changed. Refresh Hospitable and review the reply again.')
                if context.get('stay_adjustment_ids') and not context.get('automatic_safety'):
                    verify_for_reply(connect, context['stay_adjustment_ids'])
                messages = get('reservations/' + row['reservation_id'] + '/messages', token)
                fingerprint = hashlib.sha256(json.dumps(sorted(messages, key=lambda message: str(message['id'])), sort_keys=True).encode()).hexdigest()
                original = next((message for message in messages if str(message['id']) == row['message_id'] and message['sender_type'] == 'guest' and message['conversation_id'] == context['conversation_id']), None)
                if context.get('automatic_safety'):
                    from safety import POLICY_VERSION, detect, guest_message
                    if not original or detect(original['body']) != context['automatic_safety'] or hashlib.sha256(original['body'].encode()).hexdigest() != context['safety_message_hash'] or context.get('safety_policy') != POLICY_VERSION or row['body'] != guest_message(context['automatic_safety'], context['safety_city']):
                        raise ValueError('Safety report or emergency instructions changed; manager review is required.')
                    if emergency_reply_exists(messages, original['created_at']):
                        raise ValueError('The property manager has already sent emergency calling instructions.')
                    for message in messages:
                        if dt.datetime.fromisoformat(message['created_at']) <= dt.datetime.fromisoformat(original['created_at']):
                            continue
                        if message['sender_type'] == 'guest' and re.search(r'\b(?:false alarm|all clear|resolved|no longer (?:smell|smelling)|(?:no|not) (?:a )?gas leak)\b', message.get('body') or '', re.IGNORECASE):
                            raise ValueError('The guest has updated or withdrawn the safety report; manager review is required.')
                elif fingerprint != context['source_messages_fingerprint'] or not original:
                    raise ValueError('The conversation changed. Refresh Hospitable and review the reply again.')
                current = dt.datetime.now(dt.UTC)
                prearrival = bool(context.get('prearrival_reply') or context.get('automatic_safety')) and current < check_in <= current + dt.timedelta(days=2)
                if not (check_in <= current < check_out or prearrival) or current - dt.datetime.fromisoformat(row['approved_at']) > dt.timedelta(minutes=10):
                    raise ValueError('The stay ended or approval expired. Refresh and review before sending.')
                timestamp = current.isoformat()
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    live = db.execute('SELECT state, context FROM guest_reply_sends WHERE id = ?', (row['id'],)).fetchone()
                    saved = db.execute('SELECT * FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?', (row['issue_id'], row['message_id'])).fetchone()
                    if live['state'] != 'queued' or live['context'] != row['context']:
                        continue
                    if not (context.get('automatic_safety') or context.get('automatic_stay') or context.get('maintenance_question') or context.get('automatic_supply')) and (not saved or saved['version'] != context['draft_version'] or saved['body'] != row['body'] or saved['source_fingerprint'] != context['source_fingerprint']):
                        raise ValueError('The draft changed after approval. Review and approve again.')
                    if context.get('automatic_safety') and not db.execute('SELECT 1 FROM safety_alerts WHERE reservation_id = ? AND message_id = ?', (row['reservation_id'], row['message_id'])).fetchone():
                        raise ValueError('The recorded safety alert is missing; manager review is required.')
                    for expected in context.get('instruction_versions', []):
                        answer = db.execute('SELECT version FROM property_answers WHERE property_id = ? AND intent = ?', (expected['property_id'], expected['intent'])).fetchone()
                        if not answer or answer['version'] != expected['version']:
                            raise ValueError('Property instructions changed after approval. Review the latest answer before sending.')
                    if context.get('maintenance_question'):
                        link = db.execute('SELECT remote FROM maintenance_links WHERE issue_id = ?', (row['issue_id'],)).fetchone()
                        question = context['maintenance_question']
                        if saved or not link or json.loads(link['remote']).get('next_question') != question or row['body'] != question['body']:
                            raise ValueError('The maintenance intake question changed. Review the current repair.')
                    if context.get('automatic_supply'):
                        order = db.execute('SELECT message_id FROM manual_order_notices WHERE issue_id = ?', (row['issue_id'],)).fetchone()
                        if saved or not order or order['message_id'] != row['message_id'] or row['body'] != SUPPLY_ACKNOWLEDGMENT:
                            raise ValueError('The supply handoff changed or the manager prepared a reply.')
                    if context.get('automatic_stay') and saved:
                        raise ValueError('A manager draft now exists. Review it before sending.')
                    if context.get('instruction_context'):
                        from research import analyze
                        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
                        issue = next((item for item in analyze(json.loads(snapshot['body']), db)['issues'] if item['id'] == row['issue_id']), None) if snapshot else None
                        question = next((item for item in issue['evidence'] if item['id'] == row['message_id']), None) if issue else None
                        fresh = draft(db, issue, question) if question else None
                        if not fresh or fresh['instruction_context'] != context['instruction_context']:
                            raise ValueError('The instruction answer changed. Review the current answer before sending.')
                        if context.get('automatic_stay') and (
                            fresh['needs_manager'] or fresh['suggested'] != row['body']
                            or not fresh['stay_policy_approvals']
                        ):
                            raise ValueError('The stay reply no longer qualifies for automatic sending.')
                    db.execute("UPDATE guest_reply_sends SET state = 'attempting', attempted_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, row['id']))
                    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_attempted', timestamp, json.dumps({'send_id': row['id'], 'reservation_id': row['reservation_id'], 'automatic_safety': context.get('automatic_safety')})))
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
                    logging.getLogger(__name__).exception(json.dumps({'event': 'guest_reply_send_unconfirmed', 'send_id': row['id']}))
                    error = 'Send outcome is unknown. Check Hospitable before sending again.'
            except Exception as failure:
                logging.getLogger(__name__).exception(json.dumps({'event': 'guest_reply_verification_failed', 'send_id': row['id']}))
                error = str(failure) if isinstance(failure, ValueError) else 'Could not verify the current Hospitable conversation. Refresh and review before approving again.'
            with connect() as db:
                timestamp = dt.datetime.now(dt.UTC).isoformat()
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE guest_reply_sends SET state = ?, reference = ?, error = ?, updated_at = ? WHERE id = ? AND state IN (\'queued\',\'attempting\') AND context = ?', (state, reference, error, timestamp, row['id'], row['context']))
                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'guest_reply_send_' + state, timestamp, json.dumps({'send_id': row['id']})))
            logging.getLogger(__name__).info(json.dumps({'event': 'guest_reply_send_' + state, 'send_id': row['id']}))


def deliver(connect, stop, queue='standard'):
    while not stop.is_set():
        try:
            deliver_pending(connect, stop, queue)
        except Exception as error:
            logging.getLogger(__name__).exception(json.dumps({'event': 'guest_reply_worker_failed', 'queue': queue, 'error_type': type(error).__name__}))
            stop.wait(15)
