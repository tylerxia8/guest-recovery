import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from settings import load_environment, public_origin

load_environment()

ROOT = Path(__file__).resolve().parent
DATABASE = os.environ.get('DATABASE_PATH', str(ROOT / 'recovery.sqlite3'))
LIMIT = int(os.environ.get('INCIDENT_LIMIT_CENTS', '3500'))
RATE = {}
LOCK = threading.Lock()
SYNC_WAKE = threading.Event()
SYNC_STOP = threading.Event()
CATALOG = {'hand_towels': ('hand towels', 'hand towel'), 'bath_towels': ('bath towels', 'bath towel'), 'toilet_paper': ('toilet paper', 'toilet roll'), 'soap': ('soap',), 'shampoo': ('shampoo',)}


@contextmanager
def connect():
    connection = sqlite3.connect(DATABASE, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, password_hash BLOB NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','manager')), created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires_at REAL NOT NULL, login_key TEXT NOT NULL, UNIQUE(user_id, login_key));
        CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, property_id TEXT NOT NULL,
            message TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL,
            proposal TEXT, order_reference TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS open_incident ON incidents(reservation_id, category)
            WHERE status IN ('pending', 'approved', 'ordered', 'ordered_review');
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_supplier (id INTEGER PRIMARY KEY CHECK(id = 1), body TEXT NOT NULL, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS local_requests (id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','available','unavailable','cancelled','received')), notice_status TEXT NOT NULL CHECK(notice_status IN ('pending','sent','failed')), email_id TEXT, payload TEXT, error TEXT, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS active_local_request ON local_requests(issue_id) WHERE state IN ('pending','available');
        CREATE TABLE IF NOT EXISTS rental_units (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fulfillment_profiles (property_id TEXT PRIMARY KEY REFERENCES rental_units(id), imported_address TEXT NOT NULL, body TEXT, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS preferred_products (id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES rental_units(id), item_type TEXT NOT NULL, room TEXT NOT NULL, tv_brand TEXT NOT NULL, tv_model TEXT NOT NULL, title TEXT NOT NULL, asin TEXT NOT NULL, version INTEGER NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS preferred_product_per_unit ON preferred_products(property_id, item_type, lower(room)) WHERE active = 1;
        CREATE TABLE IF NOT EXISTS purchase_requests (id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, property TEXT NOT NULL, summary TEXT NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents > 0), gift_cents INTEGER NOT NULL CHECK(gift_cents >= 0 AND gift_cents <= 300 AND gift_cents <= total_cents), decision TEXT NOT NULL CHECK(decision IN ('pending', 'approved', 'vetoed')), notice_status TEXT NOT NULL CHECK(notice_status IN ('pending', 'sent', 'delivered', 'failed')), email_payload TEXT, email_id TEXT, notified_at TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS active_purchase_proposal ON purchase_requests(issue_id) WHERE decision != 'vetoed';
        CREATE TABLE IF NOT EXISTS integration_state (id INTEGER PRIMARY KEY CHECK (id = 1), status TEXT NOT NULL, last_attempt TEXT, last_success TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS worksheets (issue_id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL, source_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_snapshot (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL);
        ''')
        if "'received'" not in db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'local_requests'").fetchone()['sql']:
            db.executescript('''
            BEGIN IMMEDIATE;
            ALTER TABLE local_requests RENAME TO local_requests_previous;
            CREATE TABLE local_requests (id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','available','unavailable','cancelled','received')), notice_status TEXT NOT NULL CHECK(notice_status IN ('pending','sent','failed')), email_id TEXT, payload TEXT, error TEXT, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO local_requests SELECT * FROM local_requests_previous;
            DROP TABLE local_requests_previous;
            CREATE UNIQUE INDEX active_local_request ON local_requests(issue_id) WHERE state IN ('pending','available');
            COMMIT;
            ''')
        db.execute("CREATE TABLE IF NOT EXISTS guest_reply_sends (id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, message_id TEXT NOT NULL, issue_id TEXT NOT NULL, body TEXT NOT NULL, context TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','attempting','accepted','blocked','failed','unknown')), reference TEXT, error TEXT, approved_at TEXT NOT NULL, attempted_at TEXT, updated_at TEXT NOT NULL, UNIQUE(reservation_id, message_id))")
        db.execute('CREATE TABLE IF NOT EXISTS shared_products (id TEXT PRIMARY KEY, item_type TEXT NOT NULL, title TEXT NOT NULL, asin TEXT NOT NULL, version INTEGER NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS active_shared_product ON shared_products(item_type) WHERE active = 1')
        from manual_orders import initialize as initialize_manual_orders
        initialize_manual_orders(db)
        db.execute('CREATE TABLE IF NOT EXISTS purchase_plans (issue_id TEXT NOT NULL, message_id TEXT NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL, source_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (issue_id, message_id))')
        db.execute('CREATE TABLE IF NOT EXISTS guest_reply_drafts (issue_id TEXT NOT NULL, message_id TEXT NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL, source_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (issue_id, message_id))')
        db.execute('CREATE TABLE IF NOT EXISTS cleaner_response_links (request_id TEXT PRIMARY KEY REFERENCES local_requests(id), token_hash TEXT NOT NULL UNIQUE, fingerprint TEXT)')
        db.execute("CREATE TABLE IF NOT EXISTS cleaner_returns (id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('draft','queued','submitted','uncertain','blocked','completed','cancelled')), remote TEXT, error TEXT, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_poll TEXT NOT NULL, attempted_at TEXT)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_cleaner_return ON cleaner_returns(issue_id) WHERE state IN ('draft','queued','submitted','uncertain','blocked')")
        db.execute('CREATE TABLE IF NOT EXISTS maintenance_links (issue_id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL UNIQUE,source_fingerprint TEXT NOT NULL,remote TEXT NOT NULL,checked_at TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS maintenance_synced_messages (issue_id TEXT NOT NULL, message_id TEXT NOT NULL, PRIMARY KEY (issue_id, message_id))')
        from reply_library import initialize as initialize_reply_library
        initialize_reply_library(db)
        from safety import initialize as initialize_safety
        initialize_safety(db)
        from stay_adjustments import initialize as initialize_stay_policy
        initialize_stay_policy(db)
        if 'offer' not in {row['name'] for row in db.execute('PRAGMA table_info(purchase_requests)')}:
            db.execute('ALTER TABLE purchase_requests ADD COLUMN offer TEXT')
    os.chmod(DATABASE, 0o600)


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def required(data, field, maximum=200):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid ' + field)
    return value.strip()


def audit(db, incident_id, action, details):
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (incident_id, action, now(), json.dumps(details)))


def mutate(db, path, data):
    if path == '/api/property-instructions':
        from property_instructions import save
        return save(db, data, now())
    if path in ('/api/cleaner-returns/propose', '/api/cleaner-returns/approve-schedule', '/api/cleaner-returns/notify'):
        from cleaner_returns import schedule_action
        return schedule_action(db, path, data, now())
    if path == '/api/cleaner-returns/decline':
        from cleaner_returns import decline
        return decline(db, data, now())
    if path == '/api/cleaner-returns/retry':
        from cleaner_returns import retry
        return retry(db, data, now())
    if path == '/api/cleaner-returns/confirm':
        from cleaner_returns import confirm
        return confirm(db, data, now())
    if path == '/api/cleaner-returns':
        from cleaner_returns import change
        return change(db, data, now())
    if path == '/api/purchase-plans':
        from purchase_plans import save
        return save(db, data, now())
    if path == '/api/guest-replies/send':
        from guest_replies import approve
        return approve(db, data, now())
    if path == '/api/guest-replies':
        from guest_replies import save
        return save(db, data, now())
    if path == '/api/fulfillment':
        from fulfillment import save
        return save(db, data, now())
    if path in ('/api/local-supplier', '/api/local-requests') or path.startswith('/api/local-requests/'):
        from local_supply import change
        return change(db, path, data, now())
    if path == '/api/catalog':
        from catalog import save
        return save(db, data, now())
    if path == '/api/catalog/archive':
        if data.get('scope', 'unit') not in ('shared', 'unit'):
            raise ValueError('Choose shared supplies or a unit TV remote')
        identifier = str(uuid.UUID(required(data, 'id')))
        version = data.get('version')
        if type(version) is not int:
            raise ValueError('Invalid product version')
        if data.get('scope') == 'shared':
            cursor = db.execute('UPDATE shared_products SET active = 0, version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND active = 1', (now(), identifier, version))
        else:
            cursor = db.execute('UPDATE preferred_products SET active = 0, version = version + 1, updated_at = ? WHERE id = ? AND version = ? AND active = 1', (now(), identifier, version))
        if cursor.rowcount != 1:
            raise ValueError('Product changed; reload before removing it')
        audit(db, identifier, 'preferred_product_archived', {})
        return {'id': identifier}
    if path == '/api/manual-order/approve':
        from manual_orders import approve
        return approve(db, data, now())
    if path == '/api/purchases' or path.startswith('/api/purchases/'):
        from purchases import change
        return change(db, path, data, now())
    if path == '/api/integration/sync':
        state = db.execute('SELECT status FROM integration_state WHERE id = 1').fetchone()
        if state and state['status'] == 'syncing':
            return {'status': 'syncing'}
        SYNC_WAKE.set()
        return {'status': 'queued'}
    if path == '/api/worksheets':
        from research import analyze
        from worksheet import build, validate
        issue_id = required(data, 'issue_id')
        version = data.get('version')
        if type(version) is not int or version < 0:
            raise ValueError('Invalid worksheet version')
        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        if not snapshot:
            raise ValueError('Research snapshot is missing')
        issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == issue_id and item['hand_towel']), None)
        if not issue:
            raise ValueError('Hand-towel issue is no longer present; reload research')
        saved = db.execute('SELECT * FROM worksheets WHERE issue_id = ?', ('simulation:' + issue_id,)).fetchone()
        current = build(issue, saved)
        if version != current['version'] or data.get('source_fingerprint') != current['source_fingerprint']:
            raise ValueError('Worksheet or conversation changed; reload before saving')
        inputs = validate(data.get('inputs'))
        timestamp = now()
        db.execute('INSERT INTO worksheets VALUES (?, ?, ?, ?, ?) ON CONFLICT(issue_id) DO UPDATE SET body = excluded.body, version = excluded.version, source_fingerprint = excluded.source_fingerprint, updated_at = excluded.updated_at', ('simulation:' + issue_id, json.dumps(inputs), version + 1, current['source_fingerprint'], timestamp))
        audit(db, 'simulation:' + issue_id, 'scenario_plan_saved', {'version': version + 1, 'inputs': inputs})
        return {'saved_at': timestamp, 'version': version + 1}
    if path == '/api/intake':
        message_id = required(data, 'message_id')
        reservation = required(data, 'reservation_id')
        property_id = required(data, 'property_id')
        message = required(data, 'message', 10000)
        if data.get('sender_type') != 'guest':
            raise ValueError('Only guest messages can create incidents')
        previous = db.execute('SELECT incident_id, body FROM messages WHERE id = ?', (message_id,)).fetchone()
        if previous:
            if previous['body'] != json.dumps(data, sort_keys=True):
                raise ValueError('Message ID already used with different content')
            return {'incident_id': previous['incident_id'], 'duplicate': True}
        categories = [key for key, words in CATALOG.items() if any(re.search(r'\b' + re.escape(word) + r'\b', message.lower()) for word in words)]
        category = categories[0] if len(categories) == 1 else 'needs_review'
        existing = db.execute("SELECT id FROM incidents WHERE reservation_id = ? AND category = ? AND status IN ('pending', 'approved', 'ordered', 'ordered_review')", (reservation, category)).fetchone()
        incident_id = existing['id'] if existing else str(uuid.uuid4())
        if not existing:
            timestamp = now()
            db.execute('INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)', (incident_id, reservation, property_id, message, category, 'pending', timestamp, timestamp))
        else:
            existing_property = db.execute('SELECT property_id FROM incidents WHERE id = ?', (incident_id,)).fetchone()['property_id']
            if existing_property != property_id:
                raise ValueError('Reservation property does not match existing incident')
        if existing:
            state = db.execute('SELECT status FROM incidents WHERE id = ?', (incident_id,)).fetchone()['status']
            if state == 'approved':
                db.execute("UPDATE incidents SET status = 'pending', proposal = NULL, updated_at = ? WHERE id = ?", (now(), incident_id))
                audit(db, incident_id, 'approval_invalidated', {})
            elif state in ('ordered', 'ordered_review'):
                db.execute("UPDATE incidents SET status = 'ordered_review', updated_at = ? WHERE id = ?", (now(), incident_id))
        db.execute('INSERT INTO messages VALUES (?, ?, ?)', (message_id, incident_id, json.dumps(data, sort_keys=True)))
        audit(db, incident_id, 'message_received', {'message_id': message_id, 'grouped': bool(existing)})
        return {'incident_id': incident_id, 'duplicate': False}
    match = re.fullmatch(r'/api/incidents/([a-f0-9-]{36})/(approve|reject|order|resolve)', path)
    if not match:
        raise ValueError('Unknown action')
    incident_id, action = match.groups()
    incident = db.execute('SELECT * FROM incidents WHERE id = ?', (incident_id,)).fetchone()
    if not incident:
        raise ValueError('Incident not found')
    if required(data, 'expected_updated_at') != incident['updated_at']:
        raise ValueError('Incident changed; refresh before making a decision')
    expected = {'approve': 'pending', 'reject': 'pending', 'order': 'approved', 'resolve': 'ordered'}
    if incident['status'] != expected[action] and not (action == 'resolve' and incident['status'] == 'ordered_review') and not (action == 'reject' and incident['status'] == 'approved'):
        raise ValueError('Action is invalid for current incident status')
    details = {}
    if action == 'approve':
        title = required(data, 'product_title', 300)
        asin = required(data, 'asin', 10)
        if not re.fullmatch(r'[A-Z0-9]{10}', asin):
            raise ValueError('Invalid Amazon ASIN')
        total = data.get('total_cents')
        gift = data.get('gift_cents', 0)
        if type(total) is not int or not 0 < total <= LIMIT or type(gift) is not int or not 0 <= gift <= min(300, total):
            raise ValueError('Total exceeds incident limit or gift exceeds $3')
        delivery = dt.date.fromisoformat(required(data, 'delivery_date', 10))
        checkout = dt.date.fromisoformat(required(data, 'checkout_date', 10))
        timezone = required(data, 'property_timezone', 100)
        try:
            property_zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise ValueError('Invalid property timezone') from None
        if not dt.datetime.now(property_zone).date() <= delivery < checkout:
            raise ValueError('Delivery must be today or later and before checkout')
        if data.get('reservation_verified') is not True or data.get('delivery_verified') is not True:
            raise ValueError('Verify active reservation, property address, and delivery for all items')
        details = {'product_title': title, 'asin': asin, 'total_cents': total, 'gift_cents': gift, 'delivery_date': str(delivery), 'checkout_date': str(checkout), 'property_timezone': timezone}
        db.execute('UPDATE incidents SET proposal = ? WHERE id = ?', (json.dumps(details), incident_id))
    if action == 'order':
        reference = required(data, 'order_reference')
        db.execute('UPDATE incidents SET order_reference = ? WHERE id = ?', (reference, incident_id))
        details = {'order_reference': reference}
    status = {'approve': 'approved', 'reject': 'rejected', 'order': 'ordered', 'resolve': 'resolved'}[action]
    db.execute('UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?', (status, now(), incident_id))
    audit(db, incident_id, action, details)
    return {'incident_id': incident_id, 'status': status}


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, format, *args):
        pass

    def respond(self, status, data, content_type='application/json', headers=None):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        started = time.monotonic()
        trace = str(uuid.uuid4())
        from auth import (
            AuthError,
            cookie,
            create_user,
            login,
            new_session,
            require_csrf,
            session,
        )
        requested = self.path
        try:
            self.path = urlsplit(self.path).path
            host = self.headers.get('Host', '')
            local_hosts = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
            configured_origin = public_origin()
            remote_host = urlsplit(configured_origin).netloc if configured_origin else ''
            local_request = host in local_hosts and ipaddress.ip_address(self.client_address[0]).is_loopback and not self.headers.get('X-Forwarded-For') and not self.headers.get('X-Forwarded-Proto')
            proxy_ranges = [ipaddress.ip_network(value.strip()) for value in os.environ.get('TRUSTED_PROXY_CIDRS', '127.0.0.1/32,::1/128').split(',')]
            if any(network.prefixlen == 0 for network in proxy_ranges):
                raise ValueError('Trusted proxies cannot include all addresses')
            trusted_proxy = any(ipaddress.ip_address(self.client_address[0]) in network for network in proxy_ranges)
            railway_ingress = bool(os.environ.get('RAILWAY_PROJECT_ID') and os.environ.get('RAILWAY_SERVICE_ID') and os.environ.get('RAILWAY_ENVIRONMENT_ID') and remote_host and self.headers.get('X-Forwarded-Host') == remote_host and self.headers.get('X-Railway-Request-Id'))
            remote_request = bool(remote_host and host == remote_host and (trusted_proxy or railway_ingress) and self.headers.get('X-Forwarded-Proto') == 'https')
            health_request = self.command == 'GET' and self.path == '/health'
            if not local_request and not remote_request and not health_request:
                self.respond(403, {'error': 'Use the configured HTTPS dashboard or local address'})
                return
            origin = configured_origin if remote_request else 'http://' + host
            if self.command == 'POST' and self.headers.get('Origin') != origin:
                self.respond(403, {'error': 'Same-origin access required'})
                return
            client_address = self.client_address[0]
            if railway_ingress and remote_request:
                client_address = str(ipaddress.ip_address(self.headers.get('X-Real-IP', '')))
            elif remote_request:
                for value in reversed(self.headers.get('X-Forwarded-For', '').split(',')):
                    if value.strip():
                        address = ipaddress.ip_address(value.strip())
                        client_address = str(address)
                        if not any(address in network for network in proxy_ranges):
                            break
            with LOCK:
                current = time.monotonic()
                for address in list(RATE):
                    if current - RATE[address][0] >= 60:
                        del RATE[address]
                address = client_address
                bucket = RATE.setdefault(address, [current, 0])
                bucket[1] += 1
                if bucket[1] > 120:
                    self.respond(429, {'error': 'Rate limit exceeded'})
                    return
            if health_request:
                self.respond(200, {'status': 'ok'})
                return
            with connect() as db:
                actor = session(db, self.headers, origin)
                setup_allowed = local_request and not configured_origin and not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
            if self.command == 'GET' and self.path == '/api/auth/session':
                self.respond(200, {'authenticated': bool(actor), 'setup_allowed': setup_allowed, 'username': actor['username'] if actor else None, 'role': actor['role'] if actor else None, 'csrf': actor['csrf'] if actor else None})
                return
            assets = {'/login': ('login.html', 'text/html; charset=utf-8'), '/login.js': ('login.js', 'text/javascript'), '/auth-client.js': ('auth-client.js', 'text/javascript'), '/app.js': ('app.js', 'text/javascript'), '/catalog.js': ('catalog.js', 'text/javascript'), '/local-supply.js': ('local-supply.js', 'text/javascript'), '/guest-replies.js': ('guest-replies.js', 'text/javascript'), '/purchase-plans.js': ('purchase-plans.js', 'text/javascript'), '/manager-view.js': ('manager-view.js', 'text/javascript'), '/maintenance.js': ('maintenance.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if self.command == 'GET' and self.path == '/':
                if not actor:
                    target = requested if re.fullmatch(r'/\?(?:purchase=[a-f0-9-]{36}|supply=[A-Za-z0-9_.~%+-]{1,512})', requested) else '/'
                    self.respond(303, b'', 'text/plain', {'Location': '/login?next=' + quote(target, safe='')})
                    return
                self.respond(200, (ROOT / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                return
            assets['/manifest.webmanifest'] = ('manifest.webmanifest', 'application/manifest+json')
            assets.update({
                f'/icons/guest-recovery-{size}.png': (f'icons/guest-recovery-{size}.png', 'image/png')
                for size in (192, 512)
            })
            if self.command == 'GET' and self.path in assets:
                filename, content_type = assets[self.path]
                self.respond(200, (ROOT / 'web' / filename).read_bytes(), content_type)
                return
            if self.command == 'GET' and self.path == '/cleaner-returns.js':
                self.respond(200, (ROOT / 'web' / 'cleaner-returns.js').read_bytes(), 'text/javascript')
                return
            if self.command == 'GET' and self.path == '/property-instructions.js':
                self.respond(200, (ROOT / 'web' / 'property-instructions.js').read_bytes(), 'text/javascript')
                return
            if self.command == 'GET' and self.path in ('/cleaner-response', '/cleaner-response.js'):
                filename = 'cleaner-response.js' if self.path.endswith('.js') else 'cleaner-response.html'
                content_type = 'text/javascript' if filename.endswith('.js') else 'text/html; charset=utf-8'
                self.respond(200, (ROOT / 'web' / filename).read_bytes(), content_type)
                return
            if self.command == 'POST' and self.path in ('/api/cleaner-response/context', '/api/cleaner-response/reply'):
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON content type required')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4000 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Invalid request size')
                raw = self.rfile.read(length)
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                submit = self.path.endswith('/reply')
                if submit and not 8 <= len(self.headers.get('Idempotency-Key', '')) <= 200:
                    raise ValueError('Idempotency-Key required')
                from cleaner_response import respond
                key = hashlib.sha256(('cleaner-response\n' + str(data.get('token')) + '\n' + self.headers.get('Idempotency-Key', '')).encode()).hexdigest()
                fingerprint = hashlib.sha256(self.path.encode() + b'\n' + raw).hexdigest() if submit else None
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    previous = db.execute('SELECT * FROM operations WHERE key = ?', (key,)).fetchone() if submit else None
                    if previous:
                        if previous['fingerprint'] != fingerprint:
                            raise ValueError('Idempotency key reused with a different reply')
                        result = json.loads(previous['response'])
                    else:
                        result = respond(db, data, now(), self.path)
                        if submit:
                            db.execute('INSERT INTO operations VALUES (?, ?, ?)', (key, fingerprint, json.dumps(result)))
                self.respond(200, result)
                return
            auth_action = self.command == 'POST' and self.path in ('/api/auth/login', '/api/auth/setup', '/api/auth/logout')
            if not actor and not auth_action:
                raise AuthError(401, 'Sign in to continue')
            if self.command == 'GET' and self.path == '/api/settings/email':
                if actor['role'] != 'admin':
                    raise AuthError(403, 'Administrator access required')
                from settings import email_status
                self.respond(200, email_status())
                return
            if self.command == 'GET' and self.path == '/api/research':
                from research import analyze
                from worksheet import build
                with connect() as db:
                    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
                    source_snapshot = json.loads(snapshot['body']) if snapshot else None
                    result = analyze(source_snapshot, db) if source_snapshot else {'issues': [], 'synced_at': None}
                    from cleaner_returns import ESCALATE, guest_context_fingerprint
                    from cleaner_returns import configuration as scheduler_configuration
                    from cleaner_returns import describe as describe_return
                    result['cleaner_scheduler'] = scheduler_configuration()
                    import maintenance_bridge
                    result['maintenance'] = maintenance_bridge.configuration()
                    maintenance_links = {row['issue_id']: maintenance_bridge.describe(row) for row in db.execute('SELECT * FROM maintenance_links')}
                    synced_repairs = {}
                    for message in db.execute('SELECT issue_id, message_id FROM maintenance_synced_messages'):
                        synced_repairs.setdefault(message['issue_id'], []).append(message['message_id'])
                    for issue in result['issues']:
                        if issue['category'] == 'Maintenance':
                            issue['maintenance_synced_messages'] = synced_repairs.get(issue['id'], [])
                            issue['maintenance_fingerprint'] = maintenance_bridge.fingerprint(issue)
                            issue['maintenance_link'] = maintenance_links.get(issue['id'])
                            issue['maintenance_fingerprints'] = {message['id']: maintenance_bridge.fingerprint(issue, message) for message in issue['evidence']}
                            issue['maintenance_links'] = {message['id']: maintenance_links[maintenance_bridge.link_key(issue, message)] for message in issue['evidence'] if maintenance_bridge.link_key(issue, message) in maintenance_links}
                    for issue in result['issues']:
                        visit = db.execute('SELECT * FROM cleaner_returns WHERE issue_id = ? ORDER BY created_at DESC, id DESC LIMIT 1', (issue['id'],)).fetchone()
                        issue['cleaner_return'] = describe_return(visit) if visit else None
                        issue['cleaner_return_fingerprint'] = guest_context_fingerprint(issue)
                        if issue['cleaner_return']:
                            issue['cleaner_return']['guest_context_changed'] = issue['cleaner_return']['plan']['source_fingerprint'] != issue['cleaner_return_fingerprint']
                        issue['cleaner_return_escalate'] = any(ESCALATE.search(message['body']) for message in issue['evidence'])
                        if visit and issue['category'] == 'Cleaning':
                            plan = json.loads(visit['body'])
                            if plan.get('guest_confirmed_at') and plan.get('confirmation_fingerprint') == issue['cleaner_return_fingerprint']:
                                issue.update({'lane': 'likely_resolved', 'status': 'Guest confirmed resolved', 'recorded_status': 'Guest confirmed resolved'})
                    from local_supply import SUPPORTED, profile
                    from local_supply import describe as describe_local
                    result['local_supplier'] = profile(db)
                    result['local_supply_types'] = list(SUPPORTED)
                    for issue in result['issues']:
                        issue['local_requests'] = [describe_local(row) for row in db.execute('SELECT * FROM local_requests WHERE issue_id = ? ORDER BY created_at DESC', (issue['id'],))]
                    from guest_replies import draft as reply_draft
                    for issue in result['issues']:
                        if issue['category'] == 'Instructions':
                            from property_instructions import (
                                solution as instruction_solution,
                            )
                            from property_instructions import (
                                source_fingerprint as instruction_source_fingerprint,
                            )
                            issue['instruction_solutions'] = [instruction_solution(db, issue, message) for message in issue['evidence']]
                            issue['instruction_fingerprint'] = instruction_source_fingerprint(issue)
                        issue['guest_replies'] = [reply_draft(db, issue, message) for message in issue['evidence']]
                        if issue['category'] == 'Safety':
                            issue['safety_alerts'] = [dict(row) for row in db.execute('SELECT id, message_id, kind, state, error, updated_at FROM safety_alerts WHERE issue_id = ? ORDER BY created_at', (issue['id'],))]
                    from catalog import DEFAULTS, ITEMS
                    result['catalog'] = {'properties': [dict(row) for row in db.execute('SELECT * FROM rental_units ORDER BY name, id')], 'products': [dict(row) for row in db.execute('SELECT * FROM preferred_products WHERE active = 1 ORDER BY property_id, item_type, room')], 'item_types': [{'id': key, 'label': value[0], 'search_query': value[2]} for key, value in ITEMS.items()]}
                    result['catalog']['shared_products'] = [{**dict(row), 'property_id': 'shared', 'room': '', 'tv_brand': '', 'tv_model': ''} for row in db.execute('SELECT * FROM shared_products WHERE active = 1 ORDER BY item_type')]
                    result['catalog']['defaults'] = [{'item_type': key, 'title': value[0], 'check': value[1], 'asin': value[2], 'source_url': value[3]} for key, value in DEFAULTS.items()]
                    for shared in result['catalog']['shared_products']:
                        default = next((item for item in result['catalog']['defaults'] if item['item_type'] == shared['item_type']), None)
                        if default:
                            default.update({'title': shared['title'], 'asin': shared['asin'], 'check': 'Shared preference for all units. Verify pack contents and delivery.', 'source_url': None})
                    from fulfillment import profile as fulfillment_profile
                    from fulfillment import quantities
                    result['catalog']['fulfillment'] = [fulfillment_profile(db, unit['id']) for unit in result['catalog']['properties']]
                    for issue in result['issues']:
                        issue['fulfillment'] = next((item for item in result['catalog']['fulfillment'] if len(issue['property_ids']) == 1 and item['property_id'] == issue['property_ids'][0]), None)
                        if issue['category'] == 'Cleaning':
                            from cleaning_proposal import propose
                            timezone = issue['fulfillment']['address'].get('timezone', 'America/New_York') if issue['fulfillment'] else 'America/New_York'
                            issue['cleaner_return_proposal'] = propose(issue, timezone, now())
                        issue['supply_quantities'] = quantities(issue, issue['fulfillment'])
                        from purchase_plans import build as purchase_plan
                        issue['purchase_plans'] = [purchase_plan(db, issue, message) for message in issue['evidence']] if issue['category'] == 'Supplies' else []
                        from local_supply import LiveRequestUnavailable, live_context
                        try:
                            destination, supplier = live_context(db, issue, source_snapshot, now())
                        except LiveRequestUnavailable as error:
                            issue['cleaner_live'] = {'eligible': False, 'reason': str(error)}
                        else:
                            issue['cleaner_live'] = {'eligible': True, 'reason': None, 'profile_version': destination['version'], 'supplier_version': supplier['version'], 'evidence_fingerprint': hashlib.sha256(json.dumps(issue['evidence'], sort_keys=True).encode()).hexdigest()}
                    state = db.execute('SELECT status, last_attempt, last_success, error FROM integration_state WHERE id = 1').fetchone()
                    from mailer import email_ready
                    from purchases import (
                        describe,
                        email_settings,
                    )
                    settings = email_settings()
                    result['purchase_policy'] = {'email_configured': email_ready(settings), 'checkout_connected': False, 'simulation': True}
                    for issue in result['issues']:
                        handoff = db.execute('SELECT status, order_lines, fingerprint, approved_at FROM manual_order_notices WHERE issue_id = ?', (issue['id'],)).fetchone()
                        issue['manual_order_status'] = handoff['status'] if handoff else None
                        issue['manual_order_fingerprint'] = handoff['fingerprint'] if handoff else None
                        issue['manual_order_approved_at'] = handoff['approved_at'] if handoff else None
                        issue['manual_order_lines'] = json.loads(handoff['order_lines']) if handoff else []
                        issue['purchases'] = [describe(row) for row in db.execute('SELECT * FROM purchase_requests WHERE issue_id = ? ORDER BY created_at DESC', (issue['id'],))]
                    result['integration'] = dict(state) if state else {'status': 'starting', 'last_attempt': None, 'last_success': None, 'error': None}
                    for issue in result['issues']:
                        if issue['hand_towel']:
                            saved = db.execute('SELECT * FROM worksheets WHERE issue_id = ?', ('simulation:' + issue['id'],)).fetchone()
                            issue['worksheet'] = build(issue, saved)
                self.respond(200, result)
                return
            if self.command == 'GET' and self.path == '/api/incidents':
                with connect() as db:
                    rows = [dict(row) for row in db.execute('SELECT * FROM incidents ORDER BY created_at DESC LIMIT 200')]
                    for row in rows:
                        row['messages'] = [json.loads(message['body'])['message'] for message in db.execute('SELECT body FROM messages WHERE incident_id = ? ORDER BY rowid', (row['id'],))]
                        row['audit'] = [dict(event) for event in db.execute('SELECT action, at, details FROM audit WHERE incident_id = ? ORDER BY id', (row['id'],))]
                self.respond(200, {'incidents': rows, 'incident_limit_cents': LIMIT, 'mode': 'research'})
                return
            if self.command != 'POST':
                self.respond(404, {'error': 'Not found'})
                return
            if self.path not in ('/api/settings/maintenance', '/api/maintenance/import', '/api/maintenance/refresh', '/api/property-instructions', '/api/cleaner-returns/notify', '/api/cleaner-returns/propose', '/api/cleaner-returns/approve-schedule', '/api/cleaner-returns', '/api/cleaner-returns/decline', '/api/cleaner-returns/retry', '/api/cleaner-returns/confirm', '/api/settings/cleaner-scheduler', '/api/purchase-plans', '/api/guest-replies/send', '/api/guest-replies', '/api/intake', '/api/worksheets', '/api/integration/sync', '/api/purchases', '/api/manual-order/approve', '/api/catalog', '/api/catalog/archive', '/api/fulfillment', '/api/local-supplier', '/api/local-requests', '/api/settings/email', '/api/auth/login', '/api/auth/setup', '/api/auth/logout') and not re.fullmatch(r'/api/(?:purchases/[a-f0-9-]{36}/(?:approve|veto)|local-requests/[a-f0-9-]{36}/(?:available|unavailable|cancel|received))', self.path):
                self.respond(403, {'error': 'Research mode: fulfillment actions are disabled'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON content type required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            raw = self.rfile.read(length)
            data = json.loads(raw)
            if not isinstance(data, dict):
                self.respond(400, {'error': 'JSON object required'})
                return
            key = self.headers.get('Idempotency-Key', '')
            if not 8 <= len(key) <= 200:
                raise ValueError('Idempotency-Key required')
            if auth_action:
                if self.path == '/api/auth/login':
                    token = login(connect, data, client_address, key)
                elif self.path == '/api/auth/setup':
                    if not setup_allowed:
                        raise AuthError(403, 'Initial account setup is available only on the local computer before remote hosting is enabled')
                    with connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                            raise AuthError(403, 'Initial setup has already been completed')
                        user = create_user(db, data, 'admin')
                        token = new_session(db, user, key)
                else:
                    if actor:
                        require_csrf(actor, self.headers)
                        with connect() as db:
                            db.execute('DELETE FROM sessions WHERE token_hash = ?', (actor['token_hash'],))
                            audit(db, actor['user_id'], 'manager_signed_out', {'actor': actor['username']})
                    token = ''
                self.respond(200, {'ok': True}, headers={'Set-Cookie': cookie(token, origin)})
                return
            require_csrf(actor, self.headers)
            if self.path in ('/api/settings/email', '/api/settings/cleaner-scheduler', '/api/settings/maintenance') and actor['role'] != 'admin':
                raise AuthError(403, 'Administrator access required')
            key = hashlib.sha256((actor['user_id'] + '\n' + key).encode()).hexdigest()
            fingerprint = hashlib.sha256(self.path.encode() + b'\n' + raw).hexdigest()
            prepared_maintenance = None
            if self.path == '/api/settings/maintenance':
                from maintenance_bridge import prepare_configuration
                prepared_maintenance = prepare_configuration(data)
            prepared_scheduler = None
            if self.path == '/api/settings/cleaner-scheduler':
                from cleaner_returns import prepare_configuration
                prepared_scheduler = prepare_configuration(data)
            prepared_repair = None
            repair_remote = None
            if self.path in ('/api/maintenance/import', '/api/maintenance/refresh'):
                from maintenance_bridge import (
                    perform_change,
                    prepare_change,
                    validate_prepared,
                )
                with connect() as db:
                    current_actor = session(db, self.headers, origin)
                    if not current_actor or current_actor['user_id'] != actor['user_id']:
                        raise AuthError(401, 'Session expired or was revoked. Sign in again.')
                    previous = db.execute('SELECT * FROM operations WHERE key = ?', (key,)).fetchone()
                    if previous and previous['fingerprint'] != fingerprint:
                        raise ValueError('Idempotency key reused with different request')
                    if not previous:
                        prepared_repair = prepare_change(db, self.path, data, now())
                        validate_prepared(db, prepared_repair)
                if prepared_repair is not None:
                    repair_remote = perform_change(prepared_repair)
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current_actor = session(db, self.headers, origin)
                if not current_actor or current_actor['user_id'] != actor['user_id']:
                    raise AuthError(401, 'Session expired or was revoked. Sign in again.')
                if self.path in ('/api/settings/email', '/api/settings/cleaner-scheduler', '/api/settings/maintenance') and current_actor['role'] != 'admin':
                    raise AuthError(403, 'Administrator access required')
                previous = db.execute('SELECT * FROM operations WHERE key = ?', (key,)).fetchone()
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('Idempotency key reused with different request')
                    result = json.loads(previous['response'])
                else:
                    audit_start = db.execute('SELECT COALESCE(MAX(id), 0) FROM audit').fetchone()[0]
                    if self.path in ('/api/maintenance/import', '/api/maintenance/refresh'):
                        from maintenance_bridge import save_change
                        if prepared_repair is None or repair_remote is None:
                            raise ValueError('The maintenance request is unavailable. Reload before retrying.')
                        result = save_change(db, prepared_repair, repair_remote, now())
                    elif self.path == '/api/settings/maintenance':
                        from maintenance_bridge import configure
                        result = configure(prepared_maintenance)
                        audit(db, actor['user_id'], 'maintenance_connection_configured', {'actor': actor['username']})
                    elif self.path == '/api/settings/cleaner-scheduler':
                        from cleaner_returns import configure
                        result = configure(prepared_scheduler)
                        audit(db, actor['user_id'], 'cleaner_scheduler_configured', {'actor': actor['username']})
                    elif self.path == '/api/settings/email':
                        from settings import save_email
                        result = save_email(data)
                        audit(db, actor['user_id'], 'email_settings_updated', {'actor': actor['username']})
                    else:
                        result = mutate(db, self.path, data)
                    for event in db.execute('SELECT id, details FROM audit WHERE id > ?', (audit_start,)).fetchall():
                        details = json.loads(event['details']) | {'actor': actor['username']}
                        db.execute('UPDATE audit SET details = ? WHERE id = ?', (json.dumps(details), event['id']))
                    db.execute('INSERT INTO operations VALUES (?, ?, ?)', (key, fingerprint, json.dumps(result)))
            self.respond(200, result)
        except AuthError as error:
            self.respond(error.status, {'error': str(error)})
        except (ValueError, UnicodeDecodeError) as error:
            self.respond(400, {'error': str(error)})
        except Exception:
            logging.getLogger(__name__).exception(json.dumps({'event': 'request_failed', 'trace': trace}))
            self.respond(500, {'error': 'Internal error', 'trace': trace})
        finally:
            logging.getLogger(__name__).info(json.dumps({'event': 'request', 'trace': trace, 'method': self.command, 'duration_ms': round((time.monotonic() - started) * 1000)}))

    do_GET = dispatch
    do_POST = dispatch


def poll_hospitable():
    from hospitable import research
    finished = None
    while not SYNC_STOP.is_set():
        if finished is not None and SYNC_STOP.wait(max(0, 60 - (time.monotonic() - finished))):
            return
        SYNC_WAKE.clear()
        with connect() as db:
            db.execute("INSERT INTO integration_state VALUES (1, 'syncing', ?, NULL, NULL) ON CONFLICT(id) DO UPDATE SET status = 'syncing', last_attempt = excluded.last_attempt, error = NULL", (now(),))
        try:
            research()
        except Exception as error:
            logging.getLogger(__name__).exception(json.dumps({'event': 'hospitable_sync_failed', 'error_type': type(error).__name__}))
            with connect() as db:
                db.execute("UPDATE integration_state SET status = 'error', error = ? WHERE id = 1", ('Could not update from Hospitable. Previous data is preserved. Check the token and API availability.',))
        else:
            with connect() as db:
                db.execute("UPDATE integration_state SET status = 'connected', last_success = ?, error = NULL WHERE id = 1", (now(),))
        finished = time.monotonic()
        SYNC_WAKE.wait(300)



def main():
    if LIMIT <= 0:
        raise SystemExit('INCIDENT_LIMIT_CENTS must be positive')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    os.umask(0o077)
    initialize()
    configured_origin = public_origin()
    if configured_origin:
        with connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
                raise SystemExit('Create an administrator locally before enabling APP_PUBLIC_URL')
    bind_host = os.environ.get('BIND_HOST', '127.0.0.1')
    if bind_host != '127.0.0.1' and not configured_origin:
        raise SystemExit('Remote binding requires APP_PUBLIC_URL and an existing administrator')
    server = ThreadingHTTPServer((bind_host, int(os.environ.get('PORT', '3000'))), Handler)
    worker = threading.Thread(target=poll_hospitable, daemon=True)
    worker.start()
    from purchases import deliver_notices
    notifications = threading.Thread(target=deliver_notices, args=(connect, SYNC_STOP), daemon=True)
    notifications.start()
    from local_supply import deliver_requests
    cleaner_notices = threading.Thread(target=deliver_requests, args=(connect, SYNC_STOP), daemon=True)
    cleaner_notices.start()
    from maintenance_bridge import synchronize as synchronize_maintenance
    maintenance_worker = threading.Thread(target=synchronize_maintenance, args=(connect, SYNC_STOP), daemon=True)
    maintenance_worker.start()
    from cleaner_returns import synchronize as synchronize_returns
    cleaner_return_worker = threading.Thread(target=synchronize_returns, args=(connect, SYNC_STOP), daemon=True)
    cleaner_return_worker.start()
    from stay_adjustments import synchronize as synchronize_stay_requests
    stay_request_worker = threading.Thread(target=synchronize_stay_requests, args=(connect, SYNC_STOP), daemon=True)
    stay_request_worker.start()
    from safety import deliver as deliver_safety_alerts
    safety_notifications = threading.Thread(target=deliver_safety_alerts, args=(connect, SYNC_STOP), daemon=True)
    safety_notifications.start()
    from guest_replies import deliver
    guest_messages = threading.Thread(target=deliver, args=(connect, SYNC_STOP), daemon=True)
    guest_messages.start()
    emergency_messages = threading.Thread(target=deliver, args=(connect, SYNC_STOP, 'safety'), daemon=True)
    emergency_messages.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SYNC_STOP.set()
        SYNC_WAKE.set()
        server.server_close()
