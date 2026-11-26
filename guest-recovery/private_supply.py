"""Persist the current shared supply proposal for manager review."""

import hashlib
import json
import time
from html import escape

from auth import AuthError
from private_replies import stay_ids


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS private_supply_proposals (
            property_id TEXT NOT NULL, booking_id TEXT NOT NULL, property_name TEXT NOT NULL,
            fingerprint TEXT NOT NULL, observed_at REAL NOT NULL, checkout_at REAL NOT NULL,
            orderable INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','approved','closed')),
            prior_approval INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(property_id, booking_id)
        )
    """)


def observe(db, fields, property_name, checkout, message_id, lines, now):
    property_id, booking_id = stay_ids(fields)
    initialize(db)
    key = (property_id, booking_id)
    if not lines:
        db.execute("""
            UPDATE private_supply_proposals
            SET state='closed', prior_approval=prior_approval OR state='approved'
            WHERE property_id=? AND booking_id=?
        """, key)
        return None
    identity = {'message_id': message_id, 'items': lines}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    orderable = all(item['asin'] and item['packs'] for item in lines)
    db.execute("""
        INSERT INTO private_supply_proposals VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0)
        ON CONFLICT(property_id,booking_id) DO UPDATE SET
            property_name=excluded.property_name, observed_at=excluded.observed_at,
            checkout_at=excluded.checkout_at, orderable=excluded.orderable,
            prior_approval=private_supply_proposals.prior_approval OR
                (private_supply_proposals.state='approved' AND private_supply_proposals.fingerprint<>excluded.fingerprint),
            state=CASE WHEN private_supply_proposals.state='approved'
                        AND private_supply_proposals.fingerprint=excluded.fingerprint
                        THEN 'approved' ELSE 'pending' END,
            fingerprint=excluded.fingerprint
    """, (*key, property_name, fingerprint, now.timestamp(), checkout.timestamp(), int(orderable)))
    return db.execute("SELECT * FROM private_supply_proposals WHERE property_id=? AND booking_id=?", key).fetchone()


def approve(db, fields):
    property_id, booking_id = stay_ids(fields)
    if fields.get('approved') != 'yes':
        raise AuthError(400, 'Confirm the selected items before approving.')
    now = time.time()
    db.execute('BEGIN IMMEDIATE')
    initialize(db)
    row = db.execute(
        'SELECT * FROM private_supply_proposals WHERE property_id=? AND booking_id=?',
        (property_id, booking_id),
    ).fetchone()
    if (not row or row['fingerprint'] != fields.get('fingerprint') or not row['orderable']
            or row['state'] == 'closed' or row['checkout_at'] <= now or row['observed_at'] <= now - 600):
        raise AuthError(409, 'Review the latest supply proposal before approving.')
    if row['state'] == 'pending':
        db.execute("""
            UPDATE private_supply_proposals SET state='approved' WHERE property_id=? AND booking_id=?
        """, (property_id, booking_id))


def approval_form(csrf, fields, proposal):
    if proposal is None:
        return ''
    notice = ''
    if proposal['state'] == 'approved':
        return '<p><strong>Approved for manual ordering</strong></p>'
    if proposal['prior_approval']:
        notice = '<p>A previous proposal was approved. Check any existing order before approving changes.</p>'
    if not proposal['orderable']:
        return notice + '<p>Confirm the compatible products and pack sizes before ordering.</p>'
    hidden = {**fields, 'csrf': csrf, 'fingerprint': proposal['fingerprint']}
    inputs = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'
        for name, value in hidden.items()
    )
    return (
        notice + '<form action="/supply-approval" method="post">' + inputs
        + '<label class="entry-confirmation"><input type="checkbox" name="approved" value="yes" required>'
        + ' I checked stock, price and delivery</label>'
        + '<button type="submit">Approve for manual ordering</button></form>'
    )
