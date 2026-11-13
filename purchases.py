import datetime as dt
import json
import logging
import re
import uuid

from mailer import email_ready, resend, send_email

AUTO_LIMIT_CENTS = 2500
VETO_SECONDS = 600


def email_settings():
    from settings import email_configuration
    return email_configuration()


def describe(row):
    request = dict(row)
    current = dt.datetime.now(dt.timezone.utc)
    request['eligible_at'] = None
    if row['decision'] == 'vetoed':
        request['status'] = 'Cancelled by manager'
    elif row['notice_status'] == 'failed':
        request['status'] = 'Email outcome unconfirmed — purchase blocked' if (row['email_id'] or '').startswith('smtp:') else 'Email failed — purchase blocked'
    elif not row['notified_at'] or (row['notice_status'] != 'delivered' and not (row['email_id'] or '').startswith('smtp:')):
        request['status'] = 'Waiting for email delivery'
    elif row['total_cents'] > AUTO_LIMIT_CENTS:
        request['status'] = 'Checkout not connected' if row['decision'] == 'approved' else 'Manager approval required'
    else:
        eligible = dt.datetime.fromisoformat(row['notified_at']) + dt.timedelta(seconds=VETO_SECONDS)
        request['eligible_at'] = eligible.isoformat()
        request['status'] = '10-minute veto window' if current < eligible else 'Checkout not connected'
    request['email_evidence'] = 'Gmail accepted the message; inbox delivery is not verified.' if (row['email_id'] or '').startswith('smtp:') and row['notified_at'] else 'Email delivery confirmed by provider.' if row['notice_status'] == 'delivered' else 'Notification is not confirmed.'
    request['approval_required'] = row['total_cents'] > AUTO_LIMIT_CENTS
    request['simulation'] = True
    request.pop('email_payload')
    request.pop('email_id')
    return request


def change(db, path, data, timestamp):
    if path == '/api/purchases':
        from research import analyze
        issue_id = data.get('issue_id')
        summary = data.get('summary')
        total = data.get('total_cents')
        gift = data.get('gift_cents')
        if not isinstance(issue_id, str) or not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 1000:
            raise ValueError('Describe all proposed items')
        if type(total) is not int or not 0 < total <= 1000000 or type(gift) is not int or not 0 <= gift <= min(300, total):
            raise ValueError('Invalid total or gift cost; the gift must be included in the total and cost no more than $3')
        if data.get('all_in_confirmed') is not True:
            raise ValueError('Confirm that the total includes all items, tax, shipping, and fees')
        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        if not snapshot:
            raise ValueError('Research snapshot is missing')
        issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == issue_id and item['category'] == 'Supplies'), None)
        if not issue:
            raise ValueError('Supply request no longer exists; refresh the page')
        if db.execute("SELECT id FROM purchase_requests WHERE issue_id = ? AND decision != 'vetoed'", (issue_id,)).fetchone():
            raise ValueError('A proposal already exists. Cancel it before changing the items or total.')
        identifier = str(uuid.uuid4())
        db.execute("INSERT INTO purchase_requests (id, issue_id, property, summary, total_cents, gift_cents, decision, notice_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?)", (identifier, issue_id, issue['property'], summary.strip(), total, gift, timestamp, timestamp))
        action = 'purchase_proposed'
        details = {'total_cents': total, 'gift_cents': gift, 'simulation': True}
    else:
        match = re.fullmatch(r'/api/purchases/([a-f0-9-]{36})/(approve|veto)', path)
        if not match:
            raise ValueError('Unknown purchase action')
        identifier, action = match.groups()
        row = db.execute('SELECT * FROM purchase_requests WHERE id = ?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Purchase proposal not found')
        if row['decision'] == 'vetoed':
            raise ValueError('This proposal was cancelled')
        if action == 'approve' and (row['total_cents'] <= AUTO_LIMIT_CENTS or row['decision'] == 'approved'):
            raise ValueError('This proposal does not need another approval')
        db.execute('UPDATE purchase_requests SET decision = ?, updated_at = ? WHERE id = ?', ('approved' if action == 'approve' else 'vetoed', timestamp, identifier))
        details = {'simulation': True}
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, action, timestamp, json.dumps(details)))
    return {'id': identifier}




def deliver_notices(connect, stop):
    while not stop.is_set():
        settings = email_settings()
        if email_ready(settings):
            with connect() as db:
                rows = db.execute("SELECT * FROM purchase_requests WHERE decision != 'vetoed' AND (notice_status = 'pending' OR (notice_status = 'sent' AND notified_at IS NULL)) ORDER BY created_at LIMIT 10").fetchall()
            for row in rows:
                if stop.is_set():
                    return
                try:
                    if row['notice_status'] == 'pending':
                        with connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            current = db.execute('SELECT * FROM purchase_requests WHERE id = ?', (row['id'],)).fetchone()
                            if current['decision'] == 'vetoed' or current['notice_status'] != 'pending':
                                continue
                            payload = json.loads(current['email_payload']) if current['email_payload'] else None
                            if payload is None:
                                for name in ('NOTIFICATION_FROM', 'MANAGER_EMAIL'):
                                    if not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', settings[name]):
                                        raise ValueError('Notification addresses must be plain email addresses')
                                policy = 'Explicit approval is required before purchase.' if row['total_cents'] > AUTO_LIMIT_CENTS else 'A 10-minute veto window starts after a successful Gmail send or confirmed Resend delivery. Gmail acceptance does not guarantee inbox delivery.'
                                from settings import manager_url
                                text = '\n'.join(('[SIMULATION — no purchase will be made]', 'Proposal ID: ' + row['id'], 'Property: ' + row['property'], 'Proposed items: ' + row['summary'], 'All-in total: $' + format(row['total_cents'] / 100, '.2f'), 'Gift included: $' + format(row['gift_cents'] / 100, '.2f'), policy, 'Sign in to Guest Recovery to approve or veto this proposal:', manager_url() + '/?purchase=' + row['id'], 'Changes to items or price require cancelling this proposal and sending a new notice.'))
                                payload = {'from': settings['NOTIFICATION_FROM'], 'to': [settings['MANAGER_EMAIL']], 'subject': '[SIMULATION] Guest Recovery purchase proposal $' + format(row['total_cents'] / 100, '.2f'), 'text': text}
                                db.execute('UPDATE purchase_requests SET email_payload = ? WHERE id = ?', (json.dumps(payload), row['id']))
                            if settings['EMAIL_PROVIDER'] == 'gmail':
                                db.execute("UPDATE purchase_requests SET notice_status = 'sent', email_id = 'smtp:inflight' WHERE id = ?", (row['id'],))
                        if dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row['created_at']) >= dt.timedelta(hours=23):
                            raise ValueError('Notice expired; cancel and recreate the proposal')
                        result = send_email(settings, payload, 'purchase-notice/' + row['id'])
                        email_id = result['id']
                        if not email_id.startswith('smtp:'):
                            email_id = str(uuid.UUID(email_id))
                        with connect() as db:
                            db.execute("UPDATE purchase_requests SET email_id = ?, notice_status = 'sent', notified_at = ? WHERE id = ?", (email_id, result.get('accepted_at'), row['id']))
                            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_sent', dt.datetime.now(dt.timezone.utc).isoformat(), '{}'))
                    else:
                        if (row['email_id'] or '').startswith('smtp:'):
                            raise ValueError('Gmail send outcome is unknown; purchase remains blocked')
                        result = resend('emails/' + str(uuid.UUID(row['email_id'])), settings)
                        event = result['last_event']
                        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
                        with connect() as db:
                            if event in ('delivered', 'opened', 'clicked'):
                                db.execute("UPDATE purchase_requests SET notice_status = 'delivered', notified_at = ?, updated_at = ? WHERE id = ? AND notified_at IS NULL", (timestamp, timestamp, row['id']))
                                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_delivered', timestamp, '{}'))
                            elif event in ('bounced', 'failed', 'suppressed', 'complained'):
                                db.execute("UPDATE purchase_requests SET notice_status = 'failed', error = 'Email delivery failed', updated_at = ? WHERE id = ?", (timestamp, row['id']))
                                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_failed', timestamp, '{}'))
                except Exception as error:
                    logging.error(json.dumps({'event': 'purchase_notice_failed', 'request_id': row['id'], 'error_type': type(error).__name__}))
                    message = 'Gmail send outcome is unconfirmed. It may have arrived; check before resending. Purchase remains blocked.' if settings['EMAIL_PROVIDER'] == 'gmail' else 'Email not confirmed. Purchase remains blocked; check email configuration or cancel and recreate the notice.'
                    with connect() as db:
                        db.execute("UPDATE purchase_requests SET notice_status = 'failed', error = ? WHERE id = ?", (message, row['id']))
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_failed', dt.datetime.now(dt.timezone.utc).isoformat(), '{}'))
                if stop.wait(0.6):
                    return
        stop.wait(15)
