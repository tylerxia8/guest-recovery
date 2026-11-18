import datetime as dt
import hashlib
import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote, urlsplit


def origin(value):
    if not isinstance(value, str) or len(value) > 250 or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError('Enter the maintenance HTTPS origin')
    parsed = urlsplit(value.rstrip('/'))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Use the maintenance HTTPS origin without a path')
    return value.rstrip('/')


def configuration():
    from settings import CONFIG_LOCK
    with CONFIG_LOCK:
        url = os.environ.get('MAINTENANCE_API_URL', '')
        dashboard = os.environ.get('MAINTENANCE_DASHBOARD_URL', '')
        organization = os.environ.get('MAINTENANCE_ORGANIZATION_ID', '')
        expires = os.environ.get('MAINTENANCE_EXPIRES_AT', '')
        token = os.environ.get('MAINTENANCE_BRIDGE_TOKEN', '')
        mode = os.environ.get('MAINTENANCE_MODE', '')
        properties = os.environ.get('MAINTENANCE_PROPERTIES', '[]')
    try:
        properties = json.loads(properties)
        active = bool(origin(url) and origin(dashboard) and uuid.UUID(organization) and token and re.fullmatch(r'[A-Za-z0-9_.~-]+', token) and dt.datetime.fromisoformat(expires).tzinfo is not None and dt.datetime.fromisoformat(expires) > dt.datetime.now(dt.UTC))
        if mode not in ('demo', 'workspace') or not isinstance(properties, list):
            active = False
            properties = []
    except (ValueError, TypeError):
        active = False
        properties = []
    return {'url': url, 'dashboard_url': dashboard, 'organization_id': organization, 'expires_at': expires or None, 'configured': active, 'mode': mode, 'properties': properties}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, token, method, path, body=None, key=None):
    origin(url)
    if method != 'GET' and not (method == 'POST' and key):
        raise ValueError('Maintenance writes require an idempotency key')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Content-Type': 'application/json'}
    if key:
        headers['Idempotency-Key'] = key
    payload = json.dumps(body).encode() if body is not None else None
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        try:
            req = urllib.request.Request(url + path, data=payload, headers=headers, method=method)
            with opener.open(req, timeout=3) as response:
                raw = response.read(100001)
            if len(raw) > 100000:
                raise ValueError('Maintenance returned too much data')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError('Maintenance returned an invalid response')
            return result
        except TypeError:
            raise ValueError('Maintenance returned an invalid response') from None
        except urllib.error.HTTPError as error:
            status = error.code
            raw_error = error.read(10000)
            error.close()
            if status == 409:
                try:
                    code = json.loads(raw_error).get('error', {}).get('code')
                except (ValueError, AttributeError):
                    code = None
                if code == 'DEMO_WORKSPACE':
                    raise ValueError('Connect a real maintenance workspace before importing guest requests') from None
                if code == 'SAFETY_REVIEW_REQUIRED':
                    raise ValueError('This request needs safety review. Keep it in the existing safety workflow.') from None
            if status in (401, 403):
                raise ValueError('Maintenance access expired or was revoked. Reconnect in Settings.') from None
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Maintenance request failed (HTTP ' + str(status) + '). Open maintenance before retrying.') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt == 2:
                raise ValueError('Maintenance is unreachable. Open maintenance before retrying the import.') from None
        time.sleep(0.25 * 2 ** attempt)


def prepare_configuration(data):
    from settings import CONFIG_LOCK
    url = origin(data.get('url'))
    dashboard = origin(data.get('dashboard_url'))
    token = data.get('token', '')
    if not isinstance(token, str) or len(token) > 8000 or token and not re.fullmatch(r'[A-Za-z0-9_.~-]+', token):
        raise ValueError('Enter a valid maintenance connection token')
    with CONFIG_LOCK:
        previous = configuration()
        previous_token = os.environ.get('MAINTENANCE_BRIDGE_TOKEN', '')
    if not token and previous['url'] != url:
        raise ValueError('Provide a connection token when changing maintenance API')
    token = token or previous_token
    if not token:
        raise ValueError('Create a Guest Recovery connection token in maintenance first')
    metadata = request(url, token, 'GET', '/v1/maintenance-bridge')
    metadata = validate_metadata(metadata)
    organization = metadata['organization_id']
    expiry = metadata['expires_at']
    if previous['organization_id'] and previous['organization_id'] != organization:
        raise ValueError('Reconnect to the same maintenance organization to preserve repair links')
    return {'MAINTENANCE_API_URL': url, 'MAINTENANCE_DASHBOARD_URL': dashboard, 'MAINTENANCE_BRIDGE_TOKEN': token, 'MAINTENANCE_ORGANIZATION_ID': organization, 'MAINTENANCE_EXPIRES_AT': expiry, 'MAINTENANCE_MODE': metadata['mode'], 'MAINTENANCE_PROPERTIES': json.dumps(metadata['properties'], ensure_ascii=True)}, previous, previous_token


def configure(prepared):
    from settings import CONFIG_LOCK, persist
    values, previous, previous_token = prepared
    with CONFIG_LOCK:
        if configuration() != previous or os.environ.get('MAINTENANCE_BRIDGE_TOKEN', '') != previous_token:
            raise ValueError('The connection changed. Reload Settings before saving.')
        persist(values)
        return configuration()


def validate_metadata(metadata):
    try:
        organization = str(uuid.UUID(metadata['organization_id']))
        expiry = dt.datetime.fromisoformat(metadata['expires_at'])
        if metadata['source'] != 'guest_recovery' or metadata['mode'] not in ('demo', 'workspace') or expiry.tzinfo is None or expiry <= dt.datetime.now(dt.UTC):
            raise ValueError('Create a fresh Guest Recovery maintenance token')
        if not isinstance(metadata['properties'], list):
            raise TypeError('Maintenance returned invalid property links')
        properties = []
        for item in metadata['properties']:
            if not isinstance(item['name'], str) or not isinstance(item['label'], str) or len(item['name']) > 1000 or len(item['label']) > 1000:
                raise ValueError('Maintenance returned invalid property names')
            properties.append({'external_property_id': str(uuid.UUID(item['external_property_id'])), 'unit_id': str(uuid.UUID(item['unit_id'])), 'name': item['name'], 'label': item['label']})
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Maintenance returned incomplete connection details') from None
    return {'organization_id': organization, 'expires_at': expiry.isoformat(), 'source': 'guest_recovery', 'mode': metadata['mode'], 'properties': properties}


def fingerprint(issue, message=None):
    source = {'issue_id': issue['id'], 'property_ids': issue['property_ids'], 'reservation_id': issue['reservation_id'], 'conversation_id': issue['conversation_id']}
    source['evidence'] = issue['evidence'] if message is None else {'id': message['id'], 'body': message['body'], 'created_at': message['created_at']}
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def link_key(issue, message=None):
    key = issue['id']
    if message is not None:
        if not isinstance(message['id'], str) or not message['id']:
            raise ValueError('Choose a guest message with a stable identity')
        key += ':' + message['id']
    if not isinstance(key, str) or not 0 < len(key) <= 200 or any(ord(character) < 32 for character in key):
        raise ValueError('Choose a maintenance request with a valid identity')
    return key


def describe(row):
    return {'remote': json.loads(row['remote']), 'checked_at': row['checked_at']} if row else None


def prepare_change(db, path, data, timestamp):
    from research import analyze
    from settings import CONFIG_LOCK
    issue_id = data.get('issue_id')
    if not isinstance(issue_id, str) or not 0 < len(issue_id) <= 200:
        raise ValueError('Choose a maintenance request')
    message_id = data.get('message_id')
    key = link_key({'id': issue_id}, {'id': message_id} if message_id is not None else None)
    with CONFIG_LOCK:
        settings = configuration()
        token = os.environ.get('MAINTENANCE_BRIDGE_TOKEN', '')
    if not settings['configured']:
        raise ValueError('Connect maintenance in Settings with a current token')
    row = db.execute('SELECT * FROM maintenance_links WHERE issue_id = ?', (key,)).fetchone()
    if not row and message_id is not None and path.endswith('/refresh'):
        row = db.execute('SELECT * FROM maintenance_links WHERE issue_id = ?', (issue_id,)).fetchone()
        if row:
            key = issue_id
    prepared = {'settings': settings, 'token': token, 'issue_id': issue_id, 'message_id': message_id, 'key': key, 'row': dict(row) if row else None, 'payload': None, 'refresh': path.endswith('/refresh')}
    if prepared['refresh']:
        if not row:
            raise ValueError('Open this request in maintenance first')
        return prepared
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    issue = next((item for item in analyze(json.loads(snapshot['body']), db)['issues'] if item['id'] == issue_id and item['category'] == 'Maintenance'), None) if snapshot else None
    if not issue or len(issue['property_ids']) != 1 or not issue.get('conversation_id'):
        raise ValueError('Choose a maintenance request mapped to one property and conversation')
    message = None
    if message_id is not None:
        matches = [item for item in issue['evidence'] if item['id'] == message_id and item.get('sender_type') == 'guest']
        if len(matches) != 1:
            raise ValueError('Choose one uniquely identified guest maintenance message')
        message = matches[0]
    current_fingerprint = fingerprint(issue, message)
    if data.get('source_fingerprint') != current_fingerprint:
        raise ValueError('The guest conversation changed. Refresh before opening maintenance.')
    if row:
        return prepared
    if message is not None and db.execute('SELECT 1 FROM maintenance_links WHERE issue_id = ?', (issue_id,)).fetchone():
        raise ValueError('This conversation already has a linked repair. Review the existing repair before creating another.')
    if message is None and db.execute('SELECT 1 FROM maintenance_links WHERE substr(issue_id,1,?) = ?', (len(issue_id) + 1, issue_id + ':')).fetchone():
        raise ValueError('Choose the individual guest request to review its existing repair')
    description = message['body'].strip() if message else '\n\n'.join(item['body'] for item in issue['evidence'] if item.get('sender_type') == 'guest').strip()
    if not description or len(description) > 5000:
        raise ValueError('The maintenance evidence must contain 1–5000 characters. Review this request in maintenance directly.')
    if any(not isinstance(value, str) or not 0 < len(value) <= 200 for value in (issue['reservation_id'], issue['conversation_id'])):
        raise ValueError('This request needs valid reservation and conversation identities')
    prepared['payload'] = {'issue_id': key, 'external_property_id': str(uuid.UUID(issue['property_ids'][0])), 'reservation_id': issue['reservation_id'], 'conversation_id': issue['conversation_id'], 'description': description, 'source_fingerprint': current_fingerprint}
    return prepared


def validate_prepared(db, prepared):
    from research import analyze
    from settings import CONFIG_LOCK
    with CONFIG_LOCK:
        if configuration() != prepared['settings'] or os.environ.get('MAINTENANCE_BRIDGE_TOKEN', '') != prepared['token'] or not configuration()['configured']:
            raise ValueError('The maintenance connection changed. Reload before continuing.')
    if prepared['payload'] is None or db is None:
        return
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    issues = analyze(json.loads(snapshot['body']), db)['issues'] if snapshot else []
    issue = next((item for item in issues if item['id'] == prepared['issue_id']), None)
    matches = [item for item in issue['evidence'] if item['id'] == prepared['message_id']] if issue and prepared['message_id'] is not None else []
    if not issue or prepared['message_id'] is not None and len(matches) != 1 or fingerprint(issue, matches[0] if matches else None) != prepared['payload']['source_fingerprint']:
        raise ValueError('The guest conversation changed. Refresh before opening maintenance.')


def perform_change(prepared):
    validate_prepared(None, prepared)
    settings, token = prepared['settings'], prepared['token']
    if prepared['row'] and not prepared['refresh']:
        return json.loads(prepared['row']['remote'])
    metadata = validate_metadata(request(settings['url'], token, 'GET', '/v1/maintenance-bridge'))
    if metadata['organization_id'] != settings['organization_id'] or metadata['expires_at'] != settings['expires_at']:
        raise ValueError('The maintenance connection identity changed. Reconnect in Settings.')
    validate_prepared(None, prepared)
    path = '/v1/maintenance-bridge/requests/' + quote(prepared['key'], safe='')
    if prepared['refresh']:
        remote = request(settings['url'], token, 'GET', path)
    else:
        if metadata['mode'] != 'workspace':
            raise ValueError('Connect a real maintenance workspace before importing guest requests')
        payload = prepared['payload']
        if not any(item['external_property_id'] == payload['external_property_id'] for item in metadata['properties']):
            raise ValueError('Map this Hospitable property to a maintenance unit before importing')
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        try:
            remote = request(settings['url'], token, 'POST', '/v1/maintenance-bridge/requests', payload, key)
        except ValueError as error:
            try:
                remote = request(settings['url'], token, 'GET', path)
            except ValueError:
                raise error from None
    validate_prepared(None, prepared)
    return remote


def save_change(db, prepared, remote, timestamp):
    validate_prepared(None, prepared)
    try:
        if remote['issue_id'] != prepared['key'] or not isinstance(remote['source_fingerprint'], str) or not re.fullmatch(r'[a-f0-9]{64}', remote['source_fingerprint']) or type(remote['ticket_number']) is not int or remote['ticket_number'] <= 0:
            raise ValueError('Maintenance returned mismatched request details')
        if not isinstance(remote['ticket_id'], str):
            raise TypeError('Maintenance returned an invalid ticket identity')
        uuid.UUID(remote['ticket_id'])
        if dt.datetime.fromisoformat(remote['updated_at']).tzinfo is None:
            raise ValueError('Maintenance returned a repair timestamp without a timezone')
        if any(not isinstance(remote[field], str) or not 0 < len(remote[field]) <= 100 for field in ('status', 'priority')):
            raise ValueError('Maintenance returned invalid repair status')
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Maintenance returned incomplete repair details') from None
    remote = {field: remote[field] for field in ('issue_id', 'source_fingerprint', 'ticket_id', 'ticket_number', 'status', 'priority', 'updated_at')}
    row = db.execute('SELECT * FROM maintenance_links WHERE issue_id = ?', (prepared['key'],)).fetchone()
    if row and (row['ticket_id'] != remote['ticket_id'] or row['source_fingerprint'] != remote['source_fingerprint']):
        raise ValueError('The maintenance link changed. Review the connected workspace.')
    if row and dt.datetime.fromisoformat(remote['updated_at']) < dt.datetime.fromisoformat(json.loads(row['remote'])['updated_at']):
        return describe(row)
    db.execute('INSERT INTO maintenance_links (issue_id,ticket_id,source_fingerprint,remote,checked_at) VALUES (?,?,?,?,?) ON CONFLICT(issue_id) DO UPDATE SET remote=excluded.remote,checked_at=excluded.checked_at', (prepared['key'], remote['ticket_id'], remote['source_fingerprint'], json.dumps(remote), timestamp))
    db.execute('INSERT INTO audit (incident_id,action,at,details) VALUES (?,?,?,?)', (prepared['issue_id'], 'maintenance_imported' if not row else 'maintenance_status_refreshed', timestamp, json.dumps({'ticket_id': remote['ticket_id'], 'organization_id': prepared['settings']['organization_id'], 'message_id': prepared['message_id']})))
    logging.getLogger(__name__).info(json.dumps({'event': 'maintenance_bridge_request', 'action': 'refresh' if row else 'import', 'ticket_id': remote['ticket_id']}))
    return describe(db.execute('SELECT * FROM maintenance_links WHERE issue_id = ?', (prepared['key'],)).fetchone())
