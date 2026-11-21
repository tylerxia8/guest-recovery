import datetime as dt
import hashlib
import json
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from email.message import EmailMessage


def resend(path, settings, payload=None, key=None):
    headers = {'Authorization': 'Bearer ' + settings['RESEND_API_KEY'], 'Content-Type': 'application/json', 'User-Agent': 'GuestRecovery/1.0'}
    if key:
        headers['Idempotency-Key'] = key
    request = urllib.request.Request('https://api.resend.com/' + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Email provider returned HTTP ' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ValueError('Email provider could not be reached') from None
        time.sleep(2 ** attempt)
    raise RuntimeError('Email retry limit reached')



def email_ready(settings):
    secret = settings['SMTP_APP_PASSWORD'] if settings['EMAIL_PROVIDER'] == 'gmail' else settings['RESEND_API_KEY']
    return bool(secret and settings['NOTIFICATION_FROM'] and settings['MANAGER_EMAIL'])


def send_email(settings, payload, key):
    if settings['EMAIL_PROVIDER'] == 'resend':
        return resend('emails', settings, payload, key)
    if settings['EMAIL_PROVIDER'] != 'gmail':
        raise ValueError('Unknown email provider')
    message = EmailMessage()
    message['From'] = payload['from']
    message['To'] = ', '.join(payload['to'])
    message['Subject'] = payload['subject']
    if payload.get('reply_to'):
        message['Reply-To'] = payload['reply_to']
    identifier = hashlib.sha256(key.encode()).hexdigest()
    message['Message-ID'] = '<' + identifier + '@' + payload['from'].split('@')[-1] + '>'
    message.set_content(payload['text'])
    client = None
    for attempt in range(3):
        try:
            client = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15, context=ssl.create_default_context())
            client.login(settings['NOTIFICATION_FROM'], settings['SMTP_APP_PASSWORD'])
            break
        except (OSError, smtplib.SMTPException) as error:
            if client:
                client.close()
                client = None
            code = getattr(error, 'smtp_code', None)
            if attempt == 2 or (code is not None and not 400 <= code < 500):
                raise ValueError('Could not connect or authenticate with Gmail') from None
            time.sleep(2 ** attempt)
    if client is None:
        raise ValueError('Gmail connection failed')
    try:
        refused = client.send_message(message)
        if refused:
            raise ValueError('Gmail refused a recipient')
    finally:
        client.close()
    return {'id': 'smtp:' + identifier, 'accepted_at': dt.datetime.now(dt.timezone.utc).isoformat()}
