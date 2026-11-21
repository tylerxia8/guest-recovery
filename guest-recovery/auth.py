import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import re
import secrets
import threading
import uuid
from http.cookies import CookieError, SimpleCookie

HASH_SLOTS = threading.BoundedSemaphore(2)
SESSION_SECONDS = 43200


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def timestamp():
    return dt.datetime.now(dt.UTC).timestamp()


def credentials(data):
    username = data.get('username')
    password = data.get('password')
    if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_.@+-]{3,100}', username):
        raise ValueError('Username must be 3–100 letters, numbers, or . _ @ + -')
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Password must contain 12–256 characters')
    return username.lower(), password


def digest(password, salt):
    if not HASH_SLOTS.acquire(blocking=False):
        raise AuthError(429, 'Sign-in is busy. Try again shortly.')
    try:
        return b'pbkdf2_sha256$600000$' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000, dklen=32)
    finally:
        HASH_SLOTS.release()


def create_user(db, data, role):
    username, password = credentials(data)
    if role not in ('admin', 'manager'):
        raise ValueError('Unknown account role')
    if db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
        raise ValueError('That username already exists')
    salt = secrets.token_bytes(16)
    identifier = str(uuid.uuid4())
    password_hash = digest(password, salt)
    db.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)', (identifier, username, salt, password_hash, role, timestamp()))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, 'manager_account_created', dt.datetime.now(dt.UTC).isoformat(), json.dumps({'username': username, 'role': role})))
    return db.execute('SELECT * FROM users WHERE id = ?', (identifier,)).fetchone()


def session(db, headers, origin):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie', ''))
    except CookieError:
        return None
    value = cookie.get('__Host-gr_session' if origin.startswith('https://') else 'gr_session')
    if not value or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value.value):
        return None
    token_hash = hashlib.sha256(value.value.encode()).hexdigest()
    row = db.execute('SELECT sessions.*, users.username, users.role FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?', (token_hash, timestamp())).fetchone()
    return dict(row) if row else None


def new_session(db, user, key):
    if db.execute('SELECT 1 FROM sessions WHERE user_id = ? AND login_key = ?', (user['id'], key)).fetchone():
        raise AuthError(409, 'This sign-in attempt was already processed. Sign in again.')
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    csrf = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires_at <= ?', (timestamp(),))
    db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, ?)', (token_hash, user['id'], csrf, timestamp() + SESSION_SECONDS, key))
    return token


def login(connect, data, address, request_key):
    username, password = credentials(data)
    keys = ('address:' + address, 'username:' + username)
    current = timestamp()
    with connect() as db:
        for key in keys:
            attempts = db.execute('SELECT * FROM login_attempts WHERE key = ?', (key,)).fetchone()
            if attempts and attempts['expires_at'] > current and attempts['failures'] >= 10:
                raise AuthError(429, 'Too many failed sign-in attempts. Try again in 15 minutes.')
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    actual = digest(password, user['salt'] if user else b'\x00' * 16)
    valid = hmac.compare_digest(actual, user['password_hash'] if user else b'pbkdf2_sha256$600000$' + b'\x00' * 32) and user is not None
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not valid:
            for key in keys:
                db.execute('INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET failures = CASE WHEN expires_at > ? THEN failures + 1 ELSE 1 END, expires_at = CASE WHEN expires_at > ? THEN expires_at ELSE excluded.expires_at END', (key, current + 900, current, current))
        else:
            latest = db.execute('SELECT * FROM users WHERE id = ?', (user['id'],)).fetchone()
            if not latest or latest['password_hash'] != user['password_hash']:
                raise AuthError(401, 'Account changed. Sign in again.')
            db.execute('DELETE FROM login_attempts WHERE key = ?', (keys[1],))
            token = new_session(db, latest, request_key)
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (user['id'], 'manager_signed_in', dt.datetime.now(dt.UTC).isoformat(), json.dumps({'username': username})))
    if not valid:
        raise AuthError(401, 'Invalid username or password.')
    return token


def require_csrf(actor, headers):
    if not hmac.compare_digest(headers.get('X-CSRF-Token', ''), actor['csrf']):
        raise AuthError(403, 'Session verification failed. Refresh the page.')


def cookie(token, origin):
    name = '__Host-gr_session' if origin.startswith('https://') else 'gr_session'
    value = name + '=' + token + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + str(SESSION_SECONDS if token else 0)
    return value + ('; Secure' if origin.startswith('https://') else '')


if __name__ == '__main__':
    from guest_app import connect, initialize
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('create-user', 'reset-password'))
    parser.add_argument('username')
    parser.add_argument('--role', choices=('admin', 'manager'), default='manager')
    args = parser.parse_args()
    password = getpass.getpass('Password: ')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match')
    initialize()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if args.command == 'create-user':
            create_user(db, {'username': args.username, 'password': password}, args.role)
        else:
            username, password = credentials({'username': args.username, 'password': password})
            user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            if not user:
                raise SystemExit('Account not found')
            salt = secrets.token_bytes(16)
            db.execute('UPDATE users SET salt = ?, password_hash = ? WHERE id = ?', (salt, digest(password, salt), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id = ?', (user['id'],))
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (user['id'], 'manager_password_reset', dt.datetime.now(dt.UTC).isoformat(), '{}'))
    print('Account updated.')
