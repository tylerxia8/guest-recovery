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


def send_ready(connect, settings):
    from purchase_plans import build
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
            # Include distinct requests from the same unresolved supply conversation.
            combined = {**message, 'body': '\n'.join(dict.fromkeys(
                item['body'] for item in issue['evidence'] if item['sender_type'] == 'guest'
            ))}
            plan = build(db, issue, combined)
            if plan['blockers'] or plan['stale']:
                continue
            # A manager should get a complete checklist, never an order for a subset.
            if {line['item_type'] for line in plan['lines']} != set(issue['item_types']) - {'chocolates'}:
                continue
            lines = [f"{item['packs']} pack(s) · {item['title']}\nhttps://www.amazon.com/dp/{item['asin']}"
                     for item in plan['order_lines']]
            payload = {
                'from': settings['NOTIFICATION_FROM'], 'to': [settings['MANAGER_EMAIL']],
                'subject': 'Action required: place Amazon supply order',
                'text': '\n\n'.join([
                    'Please place this order in Amazon Business.',
                    'Property: ' + issue['property'],
                    *lines,
                    'Confirm the unit address, quantity, current price and delivery before checkout.',
                    'Open Guest Recovery for the verified delivery profile: ' + manager_url() + '/?' + urlencode({'supply': message['id']}),
                ]),
            }
            db.execute("INSERT INTO manual_order_notices VALUES (?, ?, ?, ?, 'attempting', NULL, ?)",
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
