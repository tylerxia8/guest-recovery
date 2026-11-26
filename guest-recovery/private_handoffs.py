"""Case references for requests handed off from the shared Guest Messages inbox."""

from datetime import datetime
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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


def stay_time(value):
    if not isinstance(value, str):
        return ''
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if timestamp.tzinfo is None:
        return value
    eastern = timestamp.astimezone(ZoneInfo('America/New_York'))
    return eastern.strftime('%b %d, %I:%M %p %Z').replace(' 0', ' ')

def render_cleaning_assignment(result):
    assignment = result.get('assignment')
    if not isinstance(assignment, dict):
        return ''
    content = ''
    name = assignment.get('cleaner_name')
    if isinstance(name, str) and name:
        content += f'<p>Cleaner · {escape(name)}</p>'
    start, end = (stay_time(assignment.get(field)) for field in ('start_at', 'end_at'))
    if start and end:
        content += f'<p>{escape(start)} to {escape(end)}</p>'
    notices = {
        'queued': 'Schedule email queued.',
        'sending': 'Schedule email is being submitted.',
        'accepted': 'Email provider accepted the schedule email.',
        'uncertain': 'Email outcome is uncertain. Check in Cleaner before resending.',
        'failed': 'Schedule email failed. Review it in Cleaner.',
        'stale': 'Schedule email is out of date. Review the current schedule in Cleaner.',
    }
    status = result.get('schedule_email_status')
    if isinstance(status, str) and status in notices:
        content += f'<p>{notices[status]}</p>'
    return content


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
    if kind == 'cleaning':
        content += render_cleaning_assignment(result)
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


def render_stay_decision(result, payload):
    reasons = {
        'reservation_sync_unconfirmed': 'Refresh the Hospitable import in Cleaner.',
        'outside_checkin_day': 'Choose an earlier time on the check-in date.',
        'outside_checkout_day': 'Choose a later time on the checkout date.',
        'checkin_already_due': 'The original check-in time has passed.',
        'checkout_already_due': 'The original checkout time has passed.',
        'cleaning_completion_unconfirmed': 'Cleaner needs a recorded turnover completion.',
        'property_not_ready': 'The property is still awaiting turnover completion.',
        'cleaning_window_unconfirmed': 'The remaining cleaning window could not be verified.',
        'cleaning_assignment_conflicts': 'The requested time conflicts with the cleaning assignment.',
        'cleaning_offer_requires_review': 'A cleaner offer needs review in Cleaner.',
        'schedule_confirmation_required': 'The cleaning schedule does not currently support this request.',
        'another_guest_stay_conflicts': 'The requested time overlaps another guest’s stay.',
    }
    label = 'Early check-in' if payload['kind'] == 'early_checkin' else 'Late checkout'
    if result.get('decision') == 'approved' and isinstance(result.get('target_at'), str):
        content = f'<h2>{label} approved in Cleaner</h2><p>{escape(stay_time(result["target_at"]))}</p>'
        content += '<p>Confirm the approved time with the guest.</p>'
    else:
        reason = reasons.get(result.get('reason'), 'Check the schedule in Cleaner before confirming this request.')
        content = f'<h2>{label} is pending</h2><p>{escape(reason)}</p>'
    query = escape(urlencode({
        'property_external_id': payload['property_external_id'],
        'reservation_external_id': payload['reservation_external_id'],
    }))
    return content + f'<p><a href="/messages?{query}">Back to guest messages</a></p>'
