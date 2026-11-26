"""Case references for requests handed off from the shared Guest Messages inbox."""

from html import escape
from urllib.parse import urlencode

from auth import AuthError


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS private_handoffs (
            kind TEXT NOT NULL CHECK(kind IN ('cleaning', 'repair')),
            request_id TEXT NOT NULL,
            property_id TEXT NOT NULL,
            reservation_id TEXT NOT NULL,
            PRIMARY KEY(kind, request_id)
        )
    """)


def remember(db, kind, request_id, property_id, reservation_id):
    db.execute(
        'INSERT OR IGNORE INTO private_handoffs VALUES (?, ?, ?, ?)',
        (kind, request_id, property_id, reservation_id),
    )


def case_links(db, property_id, reservation_id):
    rows = db.execute(
        'SELECT kind, request_id FROM private_handoffs WHERE property_id = ? AND reservation_id = ?'
        ' ORDER BY rowid DESC LIMIT 20',
        (property_id, reservation_id),
    ).fetchall()
    if not rows:
        return ''
    links = ''.join(f'<li>{status_link(row["kind"], row["request_id"])}</li>' for row in rows)
    return f'<h2>Cleaning and repair requests</h2><ul>{links}</ul>'


def status_link(kind, request_id):
    query = escape(urlencode({'kind': kind, 'request_id': request_id}))
    label = 'Check cleaning status' if kind == 'cleaning' else 'Check repair status'
    return f'<a href="/handoff-status?{query}">{label}</a>'


def lookup(db, query):
    kinds, identifiers = query.get('kind', []), query.get('request_id', [])
    if len(kinds) != 1 or len(identifiers) != 1 or not 1 <= len(identifiers[0]) <= 200:
        raise AuthError(400, 'Choose a cleaning or repair request.')
    row = db.execute(
        'SELECT * FROM private_handoffs WHERE kind = ? AND request_id = ?',
        (kinds[0], identifiers[0]),
    ).fetchone()
    if row is None:
        raise AuthError(404, 'This request was not found in your workspace.')
    return row


def render_status(kind, result, csrf, payload):
    from private_repair import answer_form

    if kind == 'cleaning':
        heading = 'Cleaning request'
        statuses = {'needs_manager': 'Needs scheduling', 'scheduled': 'Scheduled', 'accepted': 'Cleaner accepted',
                    'completed': 'Completed', 'cancelled': 'Cancelled'}
    else:
        heading = 'Repair request'
        statuses = {}
    status = str(result.get('status') or 'Status unavailable')
    label = statuses.get(status, status.replace('_', ' ').capitalize())
    content = f'<h2>{heading}</h2><p>{escape(label)}</p>'
    if kind == 'repair' and result.get('ticket_number') is not None:
        content += f'<p>Ticket {escape(str(result["ticket_number"]))}</p>'
    question = result.get('next_question')
    if kind == 'repair' and isinstance(question, dict) and isinstance(question.get('body'), str):
        content += '<h3>Next intake question</h3><p>' + escape(question['body']) + '</p>'
        if isinstance(question.get('field_key'), str):
            content += answer_form(csrf, payload, question)
    back = escape(urlencode({
        'property_external_id': payload['external_property_id'],
        'reservation_external_id': payload['reservation_id'],
    }))
    return content + f'<p><a href="/messages?{back}">Back to guest messages</a></p>'
