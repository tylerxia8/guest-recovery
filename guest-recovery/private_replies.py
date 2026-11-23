"""Workspace-local drafts for guest messages recently verified by Turnover."""

import hashlib
import json
import time
from html import escape
from uuid import UUID

from auth import AuthError


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS private_reply_sources (
            property_id TEXT NOT NULL, booking_id TEXT NOT NULL, message_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, expires_at REAL NOT NULL,
            PRIMARY KEY (property_id, booking_id, message_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS private_reply_drafts (
            property_id TEXT NOT NULL, booking_id TEXT NOT NULL, message_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (property_id, booking_id, message_id)
        )
    """)


def stay_ids(fields):
    try:
        return str(UUID(fields['property_external_id'])), str(UUID(fields['reservation_external_id']))
    except (KeyError, ValueError, TypeError):
        raise AuthError(400, 'Choose a verified Hospitable stay.') from None


def observe_messages(db, fields, messages):
    property_id, booking_id = stay_ids(fields)
    initialize(db)
    current = time.time()
    db.execute('DELETE FROM private_reply_sources WHERE expires_at <= ?', (current,))
    db.execute('DELETE FROM private_reply_sources WHERE property_id=? AND booking_id=?', (property_id, booking_id))
    result = {}
    for message in messages:
        message_id = message.get('id')
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256 or message.get('truncated'):
            continue
        fingerprint = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
        key = (property_id, booking_id, message_id)
        db.execute('INSERT INTO private_reply_sources VALUES (?, ?, ?, ?, ?)', (*key, fingerprint, current + 600))
        draft = db.execute(
            'SELECT fingerprint, version, body FROM private_reply_drafts WHERE property_id=? AND booking_id=? AND message_id=?',
            key,
        ).fetchone()
        result[message_id] = {
            'fingerprint': fingerprint,
            'body': draft['body'] if draft else '',
            'version': draft['version'] if draft else 0,
            'stale': bool(draft and draft['fingerprint'] != fingerprint),
        }
    return result


def save(db, fields):
    property_id, booking_id = stay_ids(fields)
    message_id = fields.get('message_id', '')
    body = fields.get('body', '').strip()
    fingerprint = fields.get('fingerprint', '')
    try:
        version = int(fields.get('version', ''))
    except ValueError:
        raise AuthError(400, 'Reload the conversation before saving a reply.') from None
    if not 1 <= len(message_id) <= 256 or not 1 <= len(body) <= 3000 or version < 0:
        raise AuthError(400, 'Enter a reply of 1–3,000 characters.')
    db.execute('BEGIN IMMEDIATE')
    initialize(db)
    key = (property_id, booking_id, message_id)
    source = db.execute(
        'SELECT fingerprint, expires_at FROM private_reply_sources WHERE property_id=? AND booking_id=? AND message_id=?',
        key,
    ).fetchone()
    if not source or source['fingerprint'] != fingerprint or source['expires_at'] <= time.time():
        raise AuthError(409, 'Reload the conversation before saving a reply.')
    draft = db.execute(
        'SELECT version, fingerprint, body FROM private_reply_drafts WHERE property_id=? AND booking_id=? AND message_id=?',
        key,
    ).fetchone()
    if draft and draft['fingerprint'] == fingerprint and draft['body'] == body:
        return
    if (draft['version'] if draft else 0) != version:
        raise AuthError(409, 'This draft changed. Reload before saving.')
    db.execute("""
        INSERT INTO private_reply_drafts VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(property_id,booking_id,message_id) DO UPDATE SET
        fingerprint=excluded.fingerprint, version=excluded.version, body=excluded.body
    """, (*key, fingerprint, version + 1, body))


def form(csrf, fields, message_id, draft):
    hidden = {
        'csrf': csrf,
        'property_external_id': fields['property_external_id'],
        'reservation_external_id': fields['reservation_external_id'],
        'message_id': message_id,
        'version': str(draft['version']),
        'fingerprint': draft['fingerprint'],
    }
    inputs = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">' for name, value in hidden.items()
    )
    stale = '<p>The guest message changed. Review the draft against the new text.</p>' if draft['stale'] else ''
    return (
        '<details><summary>Draft a reply</summary><form action="/reply-draft" method="post">'
        + inputs + stale + '<label>Reply<textarea name="body" maxlength="3000" required>'
        + escape(draft['body']) + '</textarea></label><button type="submit">Save draft</button></form></details>'
    )
