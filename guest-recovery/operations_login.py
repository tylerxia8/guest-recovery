"""Cleaner sign-in for an explicitly linked Guest Messages installation."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from uuid import UUID

from auth import AuthError, new_session
from cleaner_returns import NoRedirect

FLOW_COOKIE = '__Host-gr_operations'
WORKSPACE_COOKIE = '__Host-gr_workspace'
FLOW_VALUE = re.compile(r'[A-Za-z0-9_-]{43}')
READ_TOKEN = re.compile(r'ops_[A-Za-z0-9_-]{43}')
TOUCHUP_TOKEN = re.compile(r'opc_[A-Za-z0-9_-]{43}')
REPAIR_TOKEN = re.compile(r'opr_[A-Za-z0-9_-]{43}')
REPLY_TOKEN = re.compile(r'opg_[A-Za-z0-9_-]{43}')
READ_SESSIONS = {}
EMPTY_ACCESS = (0, '', '', '', '')
MEMBERSHIP_SESSIONS = {}
MEMBERSHIP_TOKEN = re.compile(r'opm_[A-Za-z0-9_-]{43}')
READ_LOCK = threading.Lock()


def configuration(origin):
    if not origin.startswith('https://'):
        return None
    issuer = turnover_origin()
    if not issuer:
        return None
    try:
        workspace = str(UUID(os.environ['OPERATIONS_WORKSPACE_ID']))
        account = str(UUID(os.environ['OPERATIONS_ACCOUNT_ID']))
        local_user = str(UUID(os.environ['OPERATIONS_GUEST_RECOVERY_USER_ID']))
    except (KeyError, ValueError):
        return None
    return issuer, workspace, account, local_user


def turnover_origin():
    issuer = os.environ.get('OPERATIONS_TURNOVER_ORIGIN', '')
    url = urlsplit(issuer)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
        return None
    return issuer


def start(issuer):
    verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    parameters = urlencode({'client': 'guest-recovery', 'challenge': challenge, 'state': state})
    cookie = f'{FLOW_COOKIE}={state}.{verifier}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=300'
    return issuer + '/auth/operations/authorize?' + parameters, cookie


def redeem(issuer, headers, query):
    code, state = (query.get(key, ['']) for key in ('code', 'state'))
    if any(len(values) != 1 or not FLOW_VALUE.fullmatch(values[0]) for values in (code, state)):
        raise AuthError(400, 'Start shared sign-in again.')
    try:
        cookies = SimpleCookie(headers.get('Cookie', ''))
        saved_state, verifier = cookies[FLOW_COOKIE].value.split('.')
    except (KeyError, ValueError, CookieError):
        raise AuthError(400, 'Start shared sign-in again.') from None
    if not FLOW_VALUE.fullmatch(verifier) or not hmac.compare_digest(saved_state, state[0]):
        raise AuthError(400, 'Start shared sign-in again.')
    payload = json.dumps({'client': 'guest-recovery', 'code': code[0], 'verifier': verifier}).encode()
    request = Request(issuer + '/auth/operations/redeem', data=payload, headers={'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            identity = json.loads(response.read(4096))
    except (URLError, OSError, ValueError):
        raise AuthError(401, 'Shared sign-in could not be completed. Start again.') from None
    if not isinstance(identity, dict):
        raise AuthError(401, 'Shared sign-in could not be completed.')
    return identity, 'operations:' + hashlib.sha256(code[0].encode()).hexdigest()


def finish(connect, config, headers, query):
    issuer, workspace, account, local_user = config
    identity, key = redeem(issuer, headers, query)
    if identity.get('workspace_id') != workspace or identity.get('account_id') != account:
        raise AuthError(403, 'This Lucena account is not linked to Guest Messages.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        user = db.execute('SELECT * FROM users WHERE id = ?', (local_user,)).fetchone()
        if user is None:
            raise AuthError(403, 'This Lucena account is not linked to Guest Messages.')
        token = new_session(db, user, key)
    remember_read_access(token, identity)
    return token


def remember_read_access(token, identity):
    read_token = identity.get('read_token')
    expires_in = identity.get('read_token_expires_in')
    if isinstance(read_token, str) and READ_TOKEN.fullmatch(read_token) and type(expires_in) is int and 0 < expires_in <= 900:
        now = time.monotonic()
        with READ_LOCK:
            for key, (expiry, _, _, _, _) in list(READ_SESSIONS.items()):
                if expiry <= now:
                    del READ_SESSIONS[key]
            if len(READ_SESSIONS) >= 100:
                READ_SESSIONS.pop(next(iter(READ_SESSIONS)))
            touchup = identity.get('touchup_token')
            if not isinstance(touchup, str) or not TOUCHUP_TOKEN.fullmatch(touchup):
                touchup = ''
            repair = identity.get('repair_token')
            if not isinstance(repair, str) or not REPAIR_TOKEN.fullmatch(repair):
                repair = ''
            reply = identity.get('reply_token')
            if not isinstance(reply, str) or not REPLY_TOKEN.fullmatch(reply):
                reply = ''
            READ_SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = (now + expires_in, read_token, touchup, repair, reply)


def remember_membership(session_token, membership):
    if not isinstance(membership, str) or not MEMBERSHIP_TOKEN.fullmatch(membership):
        raise AuthError(503, 'Shared workspace membership is unavailable.')
    now = time.monotonic()
    with READ_LOCK:
        for key, (expires, _) in list(MEMBERSHIP_SESSIONS.items()):
            if expires <= now:
                del MEMBERSHIP_SESSIONS[key]
        if len(MEMBERSHIP_SESSIONS) >= 100:
            MEMBERSHIP_SESSIONS.pop(next(iter(MEMBERSHIP_SESSIONS)))
        MEMBERSHIP_SESSIONS[hashlib.sha256(session_token.encode()).hexdigest()] = (now + 43200, membership)


def verify_membership(issuer, session_hash, workspace, account):
    with READ_LOCK:
        expires, membership = MEMBERSHIP_SESSIONS.get(session_hash, (0, ''))
    if expires <= time.monotonic():
        return False
    request = Request(
        issuer + '/auth/operations/membership',
        headers={'Authorization': 'Bearer ' + membership, 'Accept': 'application/json'},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=8) as response:
            identity = json.loads(response.read(4096))
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            with READ_LOCK:
                MEMBERSHIP_SESSIONS.pop(session_hash, None)
            return False
        raise AuthError(503, 'Shared workspace membership is temporarily unavailable.') from None
    except (URLError, OSError, ValueError):
        raise AuthError(503, 'Shared workspace membership is temporarily unavailable.') from None
    return isinstance(identity, dict) and identity.get('workspace_id') == workspace and identity.get('account_id') == account


def has_read_access(session_hash):
    with READ_LOCK:
        expiry, _, _, _, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
        return expiry > time.monotonic()


def forget_read_access(session_hash):
    with READ_LOCK:
        READ_SESSIONS.pop(session_hash, None)


def stay_context(issuer, session_hash, query):
    return read_stay(issuer, session_hash, query, '/stay-context')


def guest_messages(issuer, session_hash, query):
    return read_stay(issuer, session_hash, query, '/guest-messages')


def read_stay(issuer, session_hash, query, resource):
    payload = {}
    for name in ('property_external_id', 'reservation_external_id'):
        values = query.get(name, [])
        if len(values) != 1 or not 1 <= len(values[0]) <= 120:
            raise AuthError(400, 'A property and reservation are required.')
        payload[name] = values[0]
    return read_turnover(issuer, session_hash, resource + '?' + urlencode(payload))


def cleaning_status(issuer, session_hash, payload):
    query = urlencode({
        'property_external_id': payload['external_property_id'],
        'reservation_external_id': payload['reservation_id'],
        'request_id': payload['issue_id'],
    })
    return read_turnover(issuer, session_hash, '/cleaning-status?' + query)


def stays(issuer, session_hash):
    return read_turnover(issuer, session_hash, '/stays')


def read_turnover(issuer, session_hash, path):
    with READ_LOCK:
        expiry, token, _, _, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    if expiry <= time.monotonic():
        forget_read_access(session_hash)
        raise AuthError(401, 'Sign in with Cleaner again to view shared stay details.')
    request = Request(
        issuer + '/auth/operations' + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            result = json.loads(response.read(384000))
        if not isinstance(result, dict):
            raise ValueError('Invalid workspace response')
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            forget_read_access(session_hash)
            raise AuthError(401, 'Sign in with Cleaner again to view shared stay details.') from None
        if status == 429:
            raise AuthError(429, 'Wait a moment before refreshing guest messages.') from None
        if status == 404:
            raise AuthError(404, 'This Hospitable stay was not found in your workspace.') from None
        raise AuthError(503, 'Cleaner stay details are temporarily unavailable.') from None
    except (URLError, OSError, ValueError):
        raise AuthError(503, 'Cleaner stay details are temporarily unavailable.') from None
    return result



def workspace_cookie(workspace):
    return f'{WORKSPACE_COOKIE}={workspace}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={43200 if workspace else 0}'


def selected_workspace(headers):
    try:
        value = SimpleCookie(headers.get('Cookie', ''))[WORKSPACE_COOKIE].value
        return str(UUID(value)) if str(UUID(value)) == value else None
    except (CookieError, KeyError, ValueError):
        return None


def has_touchup_access(session_hash):
    with READ_LOCK:
        expiry, _, token, _, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    return bool(token and expiry > time.monotonic())


def send_touchup(issuer, session_hash, payload):
    return post_schedule_change(issuer, session_hash, 'cleaning-touchup', payload, 'task_id')


def send_stay_change(issuer, session_hash, payload):
    return post_schedule_change(issuer, session_hash, 'stay-change', payload, 'request_id')


def post_schedule_change(issuer, session_hash, operation, payload, result_key):
    with READ_LOCK:
        expiry, _, token, _, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    if not token or expiry <= time.monotonic():
        raise AuthError(401, 'Reconnect with Cleaner to request a schedule change.')
    request = Request(
        issuer + '/auth/operations/' + operation,
        data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            result = json.loads(response.read(8192))
        if not isinstance(result, dict) or not result.get(result_key):
            raise ValueError('Invalid scheduling response')
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            raise AuthError(401, 'Reconnect with Cleaner to request a schedule change.') from None
        if status == 422:
            raise AuthError(422, 'Cleaner could not verify the stay or the requested time. Refresh the case and try again.') from None
        raise AuthError(503, 'Cleaner could not confirm the scheduling request.') from None
    except (URLError, OSError, ValueError):
        raise AuthError(503, 'Cleaner could not confirm the scheduling request.') from None
    return result



def has_reply_access(session_hash):
    with READ_LOCK:
        expires, _, _, _, token = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    return bool(token and expires > time.monotonic())


def send_guest_reply(issuer, session_hash, payload):
    with READ_LOCK:
        expires, _, _, _, token = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    if not token or expires <= time.monotonic():
        raise AuthError(401, 'Reconnect with Cleaner before sending a reply.')
    request = Request(
        issuer + '/auth/operations/guest-reply',
        data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=16) as response:
            result = json.loads(response.read(2048))
        if isinstance(result, dict) and result.get('status') in ('accepted', 'rejected', 'unknown'):
            return result['status']
    except HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            raise AuthError(401, 'Reconnect with Cleaner before sending a reply.') from None
        if status == 429:
            raise AuthError(429, 'Wait 31 seconds after opening the conversation, then return to the form and try again.') from None
        if status in (400, 404, 409, 422):
            raise AuthError(status, 'Cleaner blocked this attempt. Check the latest conversation and any previous reply in Hospitable.') from None
    except (URLError, OSError, ValueError):
        pass
    return 'unknown'


def maintenance_origin():
    target = os.environ.get('OPERATIONS_MAINTENANCE_ORIGIN', '')
    parsed = urlsplit(target)
    if parsed.scheme != 'https' or not parsed.hostname or any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment)):
        return None
    return target


def has_repair_access(session_hash):
    with READ_LOCK:
        expiry, _, _, token, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    return bool(token and expiry > time.monotonic() and maintenance_origin())


def send_repair(session_hash, payload, *, answer=False):
    resource = 'operations-answers' if answer else 'operations-requests'
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() if answer else payload['issue_id']
    return request_repair(session_hash, resource, payload, key)


def repair_status(session_hash, payload):
    return request_repair(session_hash, 'operations-status?' + urlencode(payload))


def request_repair(session_hash, resource, payload=None, key=None):
    origin = maintenance_origin()
    with READ_LOCK:
        expiry, _, _, token, _ = READ_SESSIONS.get(session_hash, EMPTY_ACCESS)
    if not origin or not token or expiry <= time.monotonic():
        raise AuthError(401, 'Reconnect with Cleaner to send a repair request.')
    authorization = 'Bearer ' + token
    headers = {'Authorization': authorization, 'X-Grok-Authorization': authorization, 'Accept': 'application/json'}
    if payload is not None:
        headers.update({'Content-Type': 'application/json', 'Idempotency-Key': key})
    request = Request(
        origin + '/v1/maintenance-bridge/' + resource,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            result = json.loads(response.read(8192))
        if not isinstance(result, dict) or not result.get('ticket_id'):
            raise ValueError('Invalid repair response')
    except HTTPError as error:
        status = error.code
        error.close()
        if status == 404:
            raise AuthError(404, 'This repair was not found for the verified stay.') from None
        if status == 409:
            raise AuthError(409, 'Maintenance could not accept this case. Check the property link and whether the case requires the safety workflow.') from None
        if status in (401, 403):
            raise AuthError(403, 'Maintenance could not verify this workspace or stay.') from None
        raise AuthError(503, 'Maintenance could not confirm the repair request.') from None
    except (URLError, OSError, ValueError):
        raise AuthError(503, 'Maintenance could not confirm the repair request.') from None
    return result
