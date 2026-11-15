import hashlib
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
    elif issue['category'] == 'Instructions':
        suggested = "Thanks for reaching out. We're checking the details for your property so we can give you the correct information."
    else:
        suggested = "I'm sorry for the inconvenience, and thank you for letting us know. We're reviewing the issue and will follow up with the next steps."
    fingerprint = hashlib.sha256(json.dumps({'messages': issue['messages'], 'category': issue['category'], 'lane': issue['lane'], 'resolution_hint': issue['resolution_hint']}, sort_keys=True).encode()).hexdigest()
    saved = db.execute('SELECT * FROM guest_reply_drafts WHERE issue_id = ? AND message_id = ?', (issue['id'], message['id'])).fetchone()
    return {'message_id': message['id'], 'suggested': suggested, 'body': saved['body'] if saved else suggested, 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'draft_source_fingerprint': saved['source_fingerprint'] if saved else fingerprint, 'stale': bool(saved and saved['source_fingerprint'] != fingerprint), 'updated_at': saved['updated_at'] if saved else None, 'practice': issue['lane'] == 'practice', 'safety': bool(safety), 'sending_enabled': False}


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
