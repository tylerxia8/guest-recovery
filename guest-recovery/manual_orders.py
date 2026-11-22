"""Send a manual Amazon order checklist for eligible active supply requests."""

import datetime as dt
import json
import logging
from urllib.parse import urlencode

from mailer import send_email


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS manual_order_notices (
        issue_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        order_lines TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('attempting','sent','unknown')), reference TEXT, updated_at TEXT NOT NULL
    )""")

    if 'approved_at' not in {row['name'] for row in db.execute('PRAGMA table_info(manual_order_notices)')}:
        db.execute('ALTER TABLE manual_order_notices ADD COLUMN approved_at TEXT')


def selected_plan(db, issue, message):
    from purchase_plans import build

    combined = {**message, 'body': '\n'.join(dict.fromkeys(
        item['body'] for item in issue['evidence'] if item['sender_type'] == 'guest'
    ))}
    plan = build(db, issue, combined)
    if plan['blockers'] or plan['stale']:
        return None
    if {line['item_type'] for line in plan['lines']} != set(issue['item_types']) - {'chocolates'}:
        return None
    return plan


def approve(db, data, timestamp):
    from research import analyze

    if not isinstance(data.get('issue_id'), str) or not isinstance(data.get('fingerprint'), str):
        raise ValueError('Choose an order proposal to approve')
    row = db.execute('SELECT * FROM manual_order_notices WHERE issue_id = ?', (data['issue_id'],)).fetchone()
    if not row or row['fingerprint'] != data['fingerprint']:
        raise ValueError('Order proposal changed. Refresh before approving.')
    if row['approved_at']:
        return {'approved': True}
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    issue = next((item for item in analyze(json.loads(snapshot['body']))['issues']
                  if item['id'] == row['issue_id'] and item['lane'] == 'needs_attention'), None) if snapshot else None
    message = next((item for item in issue['evidence'] if item['id'] == row['message_id']
                    and item['sender_type'] == 'guest'), None) if issue else None
    plan = selected_plan(db, issue, message) if message else None
    if not plan or plan['source_fingerprint'] != row['fingerprint'] or plan['order_lines'] != json.loads(row['order_lines']):
        raise ValueError('This request or its selected products changed. Review the current request before ordering.')
    if db.execute("SELECT 1 FROM local_requests WHERE issue_id = ? AND state IN ('pending','available','received')", (row['issue_id'],)).fetchone():
        raise ValueError('This request has a local supply handoff. Review it before ordering.')
    db.execute('UPDATE manual_order_notices SET approved_at = ? WHERE issue_id = ?', (timestamp, row['issue_id']))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)',
               (row['issue_id'], 'manual_amazon_order_approved', timestamp, json.dumps({'fingerprint': row['fingerprint']})))
    return {'approved': True}


def send_ready(connect, settings):
    from research import analyze
    from settings import manager_url

    now = dt.datetime.now(dt.UTC)
    with connect() as db:
        sync = db.execute('SELECT status, last_success FROM integration_state WHERE id = 1').fetchone()
        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        if (not snapshot or not sync or sync['status'] != 'connected' or not sync['last_success']
                or now - dt.datetime.fromisoformat(sync['last_success']) > dt.timedelta(minutes=15)):
            return
        issues = analyze(json.loads(snapshot['body']))['issues']
    for issue in issues:
        if issue['category'] != 'Supplies' or issue['lane'] != 'needs_attention':
            continue
        message = max((item for item in issue['evidence'] if item['sender_type'] == 'guest'),
                      key=lambda item: item['created_at'], default=None)
        if not message:
            continue
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM manual_order_notices WHERE issue_id = ?', (issue['id'],)).fetchone():
                continue
            if db.execute("SELECT 1 FROM purchase_requests WHERE issue_id = ? AND decision != 'vetoed'", (issue['id'],)).fetchone():
                continue
            if db.execute("SELECT 1 FROM local_requests WHERE issue_id = ? AND state IN ('pending','available','received')", (issue['id'],)).fetchone():
                continue
            latest = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
            if not latest or latest['body'] != snapshot['body']:
                return
            plan = selected_plan(db, issue, message)
            if not plan:
                continue
            lines = [f"{item['packs']} pack(s) · {item['title']}\nhttps://www.amazon.com/dp/{item['asin']}"
                     for item in plan['order_lines']]
            payload = {
                'from': settings['NOTIFICATION_FROM'], 'to': [settings['MANAGER_EMAIL']],
                'subject': 'Action required: approve selected Amazon supplies',
                'text': '\n\n'.join([
                    'Review and approve these selected supplies in Guest Recovery.',
                    'Property: ' + issue['property'],
                    *lines,
                    'Confirm the unit address, quantity, current price and delivery before checkout.',
                    'Approve the selected products in Guest Recovery: ' + manager_url() + '/?' + urlencode({'supply': message['id']}),
                ]),
            }
            db.execute("INSERT INTO manual_order_notices (issue_id, message_id, fingerprint, order_lines, status, reference, updated_at) VALUES (?, ?, ?, ?, 'attempting', NULL, ?)",
                       (issue['id'], message['id'], plan['source_fingerprint'], json.dumps(plan['order_lines']), now.isoformat()))
        status, reference = 'unknown', None
        try:
            result = send_email(settings, payload, 'manual-order/' + plan['source_fingerprint'])
            status, reference = 'sent', result['id']
        except Exception as error:
            logging.getLogger(__name__).warning('Manual order email outcome unconfirmed (%s)', type(error).__name__)
        with connect() as db:
            db.execute('UPDATE manual_order_notices SET status = ?, reference = ?, updated_at = ? WHERE issue_id = ?',
                       (status, reference, dt.datetime.now(dt.UTC).isoformat(), issue['id']))
