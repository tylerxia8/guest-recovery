import datetime as dt
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
DATABASE = os.environ.get('DATABASE_PATH', str(ROOT / 'recovery.sqlite3'))
LIMIT = int(os.environ.get('INCIDENT_LIMIT_CENTS', '3500'))
RATE = {}
LOCK = threading.Lock()
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
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL, property_id TEXT NOT NULL,
            message TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL,
            proposal TEXT, order_reference TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS open_incident ON incidents(reservation_id, category)
            WHERE status IN ('pending', 'approved', 'ordered', 'ordered_review');
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_snapshot (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL, details TEXT NOT NULL);
        ''')
    os.chmod(DATABASE, 0o600)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def required(data, field, maximum=200):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid ' + field)
    return value.strip()


def audit(db, incident_id, action, details):
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (incident_id, action, now(), json.dumps(details)))


def mutate(db, path, data):
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

    def respond(self, status, data, content_type='application/json'):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        started = time.monotonic()
        trace = str(uuid.uuid4())
        try:
            host = self.headers.get('Host', '')
            allowed_hosts = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
            if host not in allowed_hosts or self.headers.get('Origin', 'http://' + host) != 'http://' + host:
                self.respond(403, {'error': 'Local same-origin access required'})
                return
            with LOCK:
                current = time.monotonic()
                for address in list(RATE):
                    if current - RATE[address][0] >= 60:
                        del RATE[address]
                address = self.client_address[0]
                bucket = RATE.setdefault(address, [current, 0])
                bucket[1] += 1
                if bucket[1] > 120:
                    self.respond(429, {'error': 'Rate limit exceeded'})
                    return
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if self.command == 'GET' and self.path in assets:
                filename, content_type = assets[self.path]
                self.respond(200, (ROOT / 'web' / filename).read_bytes(), content_type)
                return
            if self.command == 'GET' and self.path == '/api/research':
                from research import analyze
                with connect() as db:
                    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
                self.respond(200, analyze(json.loads(snapshot['body'])) if snapshot else {'issues': [], 'synced_at': None})
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
            if self.path != '/api/intake':
                self.respond(403, {'error': 'Research mode: fulfillment actions are disabled'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('JSON content type required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            key = self.headers.get('Idempotency-Key', '')
            if not 8 <= len(key) <= 200:
                raise ValueError('Idempotency-Key required')
            raw = self.rfile.read(length)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            fingerprint = hashlib.sha256(self.path.encode() + b'\n' + raw).hexdigest()
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT * FROM operations WHERE key = ?', (key,)).fetchone()
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise ValueError('Idempotency key reused with different request')
                    result = json.loads(previous['response'])
                else:
                    result = mutate(db, self.path, data)
                    db.execute('INSERT INTO operations VALUES (?, ?, ?)', (key, fingerprint, json.dumps(result)))
            self.respond(200, result)
        except (ValueError, UnicodeDecodeError) as error:
            self.respond(400, {'error': str(error)})
        except Exception:
            logging.error(json.dumps({'event': 'request_failed', 'trace': trace}))
            self.respond(500, {'error': 'Internal error', 'trace': trace})
        finally:
            logging.info(json.dumps({'event': 'request', 'trace': trace, 'method': self.command, 'duration_ms': round((time.monotonic() - started) * 1000)}))

    do_GET = dispatch
    do_POST = dispatch


if __name__ == '__main__':
    if LIMIT <= 0:
        raise SystemExit('INCIDENT_LIMIT_CENTS must be positive')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    os.umask(0o077)
    initialize()
    server = ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('PORT', '3000'))), Handler)
    server.serve_forever()
