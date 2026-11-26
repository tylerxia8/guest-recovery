"""Manager-approved property facts for the shared inbox."""

import datetime as dt
import hashlib
import json
import time
from html import escape

from auth import AuthError
from private_replies import stay_ids
from property_instructions import validated_answer
from reply_library import QUESTIONS, decision_request, intents

GUIDE_TOPICS = frozenset({
    'parking_location', 'parking_overflow', 'parking_capacity',
    'trash_location', 'trash_rules', 'trash_collection', 'checkout_steps',
})


def guides(db, property_id, message):
    question = message['body']
    if decision_request(question):
        return []
    topics = set(intents(question))
    if not topics or topics - GUIDE_TOPICS:
        return []
    result = []
    for intent in sorted(topics):
        row = db.execute(
            'SELECT body, version FROM property_answers WHERE property_id=? AND intent=?',
            (property_id, intent),
        ).fetchone()
        result.append({
            'intent': intent, 'body': row['body'] if row else '', 'version': row['version'] if row else 0,
        })
    return result


def approve(db, fields):
    property_id, booking_id = stay_ids(fields)
    intent = fields.get('intent')
    if intent not in GUIDE_TOPICS or fields.get('approved') != 'yes':
        raise AuthError(400, 'Approve a supported property instruction.')
    try:
        body = validated_answer(fields.get('body'))
    except ValueError as error:
        raise AuthError(400, str(error)) from None
    db.execute('BEGIN IMMEDIATE')
    source = db.execute("""
        SELECT 1 FROM private_reply_sources
        WHERE property_id=? AND booking_id=? AND message_id=? AND fingerprint=? AND expires_at>?
    """, (property_id, booking_id, fields.get('message_id'), fields.get('fingerprint'), time.time())).fetchone()
    if not source:
        raise AuthError(409, 'Reload the conversation before saving a property instruction.')
    row = db.execute(
        'SELECT version FROM property_answers WHERE property_id=? AND intent=?', (property_id, intent)
    ).fetchone()
    version = row['version'] if row else 0
    if fields.get('version') != str(version):
        raise AuthError(409, 'A manager changed this instruction. Reload before saving.')
    timestamp = dt.datetime.now(dt.UTC).isoformat()
    db.execute("""
        INSERT INTO property_answers (property_id,intent,body,version,updated_at,question)
        VALUES (?,?,?,?,?,?) ON CONFLICT(property_id,intent) DO UPDATE SET
        body=excluded.body, version=excluded.version, updated_at=excluded.updated_at, question=excluded.question
    """, (property_id, intent, body, version + 1, timestamp, QUESTIONS[intent]))
    db.execute('INSERT INTO audit (incident_id,action,at,details) VALUES (?,?,?,?)', (
        booking_id + ':instructions', 'property_answer_approved', timestamp,
        json.dumps({'property_id': property_id, 'intent': intent, 'version': version + 1,
                    'body_hash': hashlib.sha256(body.encode()).hexdigest()}),
    ))


def guide_form(csrf, fields, message_id, draft, guide):
    values = {
        **fields,
        'csrf': csrf,
        'message_id': message_id,
        'fingerprint': draft['fingerprint'],
        'intent': guide['intent'],
        'version': str(guide['version']),
    }
    inputs = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'
        for name, value in values.items()
    )
    title = 'Edit property instruction' if guide['version'] else 'Add property instruction'
    label = 'guide-' + hashlib.sha256((message_id + ':' + guide['intent']).encode()).hexdigest()[:20]
    return (
        '<details><summary>' + title + '</summary><form action="/property-guide" method="post">' + inputs
        + f'<label><span id="{label}">' + escape(QUESTIONS[guide['intent']]) + '</span>'
        + f'<textarea name="body" aria-labelledby="{label}" maxlength="2000" required>'
        + escape(guide['body']) + '</textarea></label>'
        + '<label class="entry-confirmation"><input type="checkbox" name="approved" value="yes" required>'
        + ' Use this answer for future guests at this property</label>'
        + '<button type="submit">Save property instruction</button></form></details>'
    )
