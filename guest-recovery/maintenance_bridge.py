"""Manager-reviewed repair handoffs to the Maintenance workspace."""

import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configured():
    return bool(os.environ.get('MAINTENANCE_ORIGIN') and os.environ.get('MAINTENANCE_CONNECTION_TOKEN'))


def call(path, payload=None):
    if not configured():
        raise ValueError('Configure the Maintenance workspace connection first.')
    origin = os.environ['MAINTENANCE_ORIGIN'].rstrip('/')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Maintenance connection requires an HTTPS origin.')
    headers = {'Authorization': 'Bearer ' + os.environ['MAINTENANCE_CONNECTION_TOKEN'],
               'Accept': 'application/json'}
    body = None
    if payload is not None:
        body = json.dumps(payload, sort_keys=True).encode()
        headers.update({'Content-Type': 'application/json',
                        'Idempotency-Key': hashlib.sha256(body).hexdigest()})
    request = Request(origin + '/v1/maintenance-bridge' + path, body, headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            return json.loads(response.read(262144))
    except HTTPError as error:
        messages = {
            401: 'The Maintenance connection has expired. Generate a fresh connection token.',
            403: 'The Maintenance connection is not authorized for this action.',
            404: 'Maintenance could not find the linked request.',
            409: 'Maintenance blocked the handoff. Check the property mapping, workspace mode, and safety queue.',
            429: 'Maintenance is rate limiting requests. Try again shortly.',
        }
        raise ValueError(messages.get(error.code, 'Maintenance did not accept the request. Try again.')) from None
    except (URLError, TimeoutError, ValueError):
        raise ValueError('Could not verify the Maintenance response. Try again.') from None


def attach(db, issue):
    from issue_workflow import fingerprint

    if issue['category'] != 'Maintenance':
        return
    row = db.execute('SELECT body FROM maintenance_repairs WHERE issue_id = ?', (issue['id'],)).fetchone()
    issue['maintenance'] = {
        'available': configured(),
        'eligible': issue['lane'] == 'needs_attention' and len(issue['property_ids']) == 1
        and bool(issue['conversation_id'] and issue['source_messages_fingerprint']),
        'fingerprint': fingerprint(issue),
        'ticket': json.loads(row['body']) if row else None,
    }


def change(db, path, data, timestamp):
    from app import audit
    from research import analyze

    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    issues = analyze(json.loads(snapshot['body']))['issues'] if snapshot else []
    issue = next((item for item in issues if item['id'] == data.get('issue_id')
                  and item['category'] == 'Maintenance'), None)
    if not issue:
        raise ValueError('Refresh the inbox and select a maintenance issue.')
    attach(db, issue)
    bridge = issue['maintenance']
    if path.endswith('/status'):
        if not bridge['ticket']:
            raise ValueError('Send the repair request before refreshing its status.')
        ticket = call('/requests/' + quote(issue['id'], safe=''))
    else:
        if not bridge['eligible'] or data.get('fingerprint') != bridge['fingerprint']:
            raise ValueError('This issue is no longer eligible or changed. Refresh and review it again.')
        report_ids = {message['id'] for message in issue['evidence']}
        if any(other['category'] == 'Safety' and other['reservation_id'] == issue['reservation_id']
               and any(message['id'] in report_ids for message in other['evidence']) for other in issues):
            raise ValueError('Review this report in the safety workflow.')
        description = data.get('description')
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 5000:
            raise ValueError('Enter a repair summary of up to 5000 characters.')
        ticket = call('/requests', {
            'issue_id': issue['id'], 'external_property_id': issue['property_ids'][0],
            'reservation_id': issue['reservation_id'], 'conversation_id': issue['conversation_id'],
            'description': description.strip(), 'source_fingerprint': issue['source_messages_fingerprint'],
        })
    if (not isinstance(ticket, dict) or ticket.get('issue_id') != issue['id']
            or type(ticket.get('ticket_number')) is not int
            or not all(isinstance(ticket.get(key), str) and ticket[key]
                       for key in ('ticket_id', 'status', 'priority', 'updated_at'))):
        raise ValueError('Maintenance returned an unexpected ticket. Try again.')
    repair = {key: ticket[key] for key in ('ticket_id', 'ticket_number', 'status', 'priority', 'updated_at')}
    db.execute('INSERT INTO maintenance_repairs VALUES (?, ?) ON CONFLICT(issue_id) DO UPDATE SET body=excluded.body',
               (issue['id'], json.dumps(repair)))
    audit(db, issue['id'], 'maintenance_status_checked' if path.endswith('/status') else 'maintenance_handoff_recorded',
          {'ticket_id': repair['ticket_id'], 'at': timestamp})
    return repair
