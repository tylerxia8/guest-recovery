import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CONFIG_LOCK = threading.RLock()
ENV_PATH = Path(os.environ.get('ENV_FILE', str(ROOT / '.env')))


def load_environment():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def public_origin():
    value = os.environ.get('APP_PUBLIC_URL', '').rstrip('/')
    if not value:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('APP_PUBLIC_URL must be an HTTPS origin without a path')
    host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
    return 'https://' + host


def manager_url():
    return public_origin() or 'http://localhost:' + os.environ.get('PORT', '3000')


def email_configuration():
    with CONFIG_LOCK:
        settings = {name: os.environ.get(name, '') for name in ('RESEND_API_KEY', 'SMTP_APP_PASSWORD', 'NOTIFICATION_FROM', 'MANAGER_EMAIL')}
        settings['EMAIL_PROVIDER'] = os.environ.get('EMAIL_PROVIDER') or 'gmail'
        return settings


def email_status():
    from mailer import email_ready
    settings = email_configuration()
    return {'provider': settings['EMAIL_PROVIDER'], 'api_key_configured': bool(settings['RESEND_API_KEY']), 'app_password_configured': bool(settings['SMTP_APP_PASSWORD']), 'configured': email_ready(settings), 'sender': settings['NOTIFICATION_FROM'], 'recipient': settings['MANAGER_EMAIL'], 'public_url': public_origin(), 'remote_ready': bool(public_origin())}


def save_email(data):
    with CONFIG_LOCK:
        return write_email(data)


def write_email(data):
    if not isinstance(data, dict):
        raise ValueError('Email settings must be an object')
    provider = data.get('provider')
    if provider not in ('gmail', 'resend'):
        raise ValueError('Choose Gmail or Resend')
    values = {'EMAIL_PROVIDER': provider}
    for field, name in [('sender', 'NOTIFICATION_FROM'), ('recipient', 'MANAGER_EMAIL')]:
        value = data.get(field)
        if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', value):
            raise ValueError('Enter plain sender and recipient email addresses')
        values[name] = value
    key = data.get('api_key', '')
    if not isinstance(key, str) or len(key) > 500 or any(character.isspace() for character in key) or any(character in key for character in ('\x00', '"', "'", '=')):
        raise ValueError('Invalid email API key')
    if key:
        values['RESEND_API_KEY'] = key
    password = data.get('app_password', '')
    if not isinstance(password, str) or len(password) > 100:
        raise ValueError('Enter a Google app password')
    password = password.replace(' ', '')
    if password:
        if not re.fullmatch(r'[a-zA-Z0-9]{16}', password):
            raise ValueError('Use a 16-character Google app password, not your normal Gmail password')
        values['SMTP_APP_PASSWORD'] = password
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    remaining = [line for line in lines if line.partition('=')[0].strip() not in values]
    content = '\n'.join(remaining + [name + '=' + value for name, value in values.items()]) + '\n'
    filename = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=ENV_PATH.parent, prefix='.env.', delete=False) as output:
            filename = output.name
            os.chmod(filename, 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(filename, ENV_PATH)
    finally:
        if filename and os.path.exists(filename):
            os.unlink(filename)
    os.environ.update(values)
    return email_status()
