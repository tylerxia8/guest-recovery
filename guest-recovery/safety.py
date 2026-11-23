import datetime as dt
import hashlib
import json
import logging
import re
import smtplib
import sqlite3

from mailer import email_ready, send_email
from settings import email_configuration, public_origin

POLICY_VERSION = '2026-11-17-v1'
KINDS = {'gas', 'fire', 'carbon_monoxide', 'electrical', 'intrusion', 'alarm_fault'}
GAS_EMERGENCY_NUMBER = '800-752-7504'
GAS_SOURCE_URL = 'https://news.duke-energy.com/releases/piedmont-natural-gas-shares-simple-steps-to-recognize-natural-gas-leaks-as-winter-heating-season-begins'


def detect(text):
    if not isinstance(text, str):
        raise TypeError('Safety reports must be text')
    text = text.casefold().replace('\u2019', "'")
    alarm_fault = False
    for clause in re.split(r'[.!?;\n]+|\bbut\b', text):
        clause = re.sub(r"\b(?:no|not|never|don.t|do not|doesn.t|does not|can.t|cannot|without)\s+(?:(?:a|any)\s+)?(?:(?:smell(?:s|ing)?|odou?r)\s+(?:something\s+)?(?:(?:like|of)\s+)?(?:any\s+)?(?:natural\s+)?gas|(?:natural\s+)?gas (?:smell|odou?r|leak)|smoke(?!\s+(?:alarm|detector))|on fire|sparks?|exposed wir\w*|(?:a\s+)?break[ -]?in)\b", '', clause)
        if re.search(r'\b(?:(?:smell(?:s|ing)?|odou?r)\s+(?:something\s+)?(?:(?:like|of)\s+)?(?:natural\s+)?gas|(?:natural\s+)?gas (?:leak|smell|odou?r)|rotten[ -]egg(?:s| smell| odor)?)\b', clause):
            return 'gas'
        if re.search(r'\b(?:carbon monoxide|co) (?:alarm|detector)\b', clause):
            if re.search(r'\b(?:sounding|going off|goes off|went off|alarming|activated|beeping|detected)\b', clause) and not re.search(r"\b(?:(?:not|isn.t|aren.t|wasn.t|weren.t) (?:sounding|going off|beeping|activated|alarming)|(?:doesn.t|don.t|didn.t) (?:sound|go off|beep|activate)|stopped|low batter\w*|chirp\w*)\b", clause):
                return 'carbon_monoxide'
            if re.search(r'\b(?:dead|broken|missing|not working|doesn.t work|no batter|removed|disconnected|expired|low batter|chirp)\w*\b|\bno (?:\w+\s+){0,4}(?:alarm|detector)\b', clause):
                alarm_fault = True
        if re.search(r'\b(?:smoke|fire)(?:/carbon monoxide)? (?:alarm|detector)\b', clause):
            if re.search(r'\b(?:sounding|going off|goes off|went off|activated|beeping|alarming)\b', clause) and not re.search(r"\b(?:(?:not|isn.t|aren.t|wasn.t|weren.t) (?:sounding|going off|beeping|activated|alarming)|(?:doesn.t|don.t|didn.t) (?:sound|go off|beep|activate)|stopped|low batter\w*|chirp\w*)\b", clause):
                return 'fire'
            if re.search(r'\b(?:dead|broken|missing|not working|doesn.t work|no batter|removed|disconnected|expired|low batter|chirp)\w*\b|\bno (?:\w+\s+){0,4}(?:alarm|detector)\b', clause):
                alarm_fault = True
        if re.search(r'\b(?:smoke (?:coming|detected|filling|pouring)|there(?:\x27s| is) (?:a )?fire|(?:a|the) fire (?:in|inside|near|at|on) (?:the )?(?:house|home|property|room|kitchen|hall|bedroom|bathroom|oven|stove|outlet|building)|on fire|(?:room|house|home|kitchen|oven|stove|outlet|socket|wall|bed) (?:is |was )?burning|flames (?:coming|from|in))\b', clause):
            return 'fire'
        if re.search(r'\b(?:exposed (?:electrical )?wir\w*|(?:outlet|socket|plug|wire|wiring|electrical|breaker|appliance|stove|oven).{0,45}\b(?:sparks?|sparking)|(?:sparks?|sparking).{0,45}\b(?:outlet|socket|plug|wire|wiring|electrical|breaker|appliance|stove|oven))\b', clause):
            return 'electrical'
        clause = re.sub(r"\b(?:no intruder|not an? intruder|no burglar|not an? burglar|not an? break[ -]?in)\b", '', clause)
        if re.search(r"\b(?:someone (?:is |was )?(?:breaking|trying to break|broke) (?:in|into)|intruder|burglar|(?:a|the) break[ -]?in|(?:house|home|property|unit|apartment|rental|place).{0,30}\bbroken into)\b", clause):
            return 'intrusion'
    return 'alarm_fault' if alarm_fault else None


def guest_message(kind, city='Charlotte'):
    if kind not in KINDS:
        raise ValueError('Unknown safety report type')
    if kind == 'alarm_fault':
        return 'Please contact the property manager immediately about the faulty or missing safety alarm. If an alarm is sounding, you smell gas or smoke, or anyone feels unwell, leave the area and call 911 immediately from a safe location. Do not disable any other alarms.'
    if kind == 'gas':
        utility = ' Also call Piedmont Natural Gas at ' + GAS_EMERGENCY_NUMBER + '.' if city.strip().casefold() == 'charlotte' else ''
        return 'Please leave the property and move away from the gas smell immediately. Once safely away, call 911 immediately.' + utility + ' Do not use your phone, electrical switches, appliances, flames, or matches in the affected area. Do not try to find or stop the leak. Stay away until emergency responders or the gas utility say it is safe to return.'
    if kind == 'intrusion':
        return 'Move to a safe place away from the intruder if you can do so safely, and call 911 immediately. Do not confront anyone or re-enter the property. Contact the property manager as soon as it is safe.'
    if kind == 'carbon_monoxide':
        return 'Leave the property immediately and get everyone into fresh air. Call 911 immediately from a safe location, and do not return until emergency responders say it is safe. Do not stop to investigate or silence the alarm. Contact the property manager once safely away.'
    return 'Leave the affected area immediately if you can do so safely, and call 911 immediately from a safe location. Do not touch exposed wiring, electrical equipment, or the source of smoke or fire. Stay away until emergency responders say it is safe to return. Contact the property manager once safely away.'


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS safety_activation (id INTEGER PRIMARY KEY CHECK(id = 1), activated_at TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO safety_activation VALUES (1, ?)', (dt.datetime.now(dt.UTC).isoformat(),))
    db.execute("CREATE TABLE IF NOT EXISTS safety_alerts (id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, reservation_id TEXT NOT NULL, message_id TEXT NOT NULL, property_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','sending','sent','unknown')), email_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_attempt TEXT NOT NULL, UNIQUE(reservation_id, message_id))")
    db.execute('CREATE INDEX IF NOT EXISTS safety_alerts_pending ON safety_alerts(state, next_attempt)')


def ingest(db, snapshot, timestamp):
    current = dt.datetime.fromisoformat(timestamp)
    if current.tzinfo is None:
        raise ValueError('Safety intake requires a timezone')
    synced = dt.datetime.fromisoformat(snapshot['synced_at'])
    if not dt.timedelta(0) <= current - synced <= dt.timedelta(minutes=10):
        return []
    activation = db.execute('SELECT activated_at FROM safety_activation WHERE id = 1').fetchone()
    if not activation:
        raise ValueError('Initialize safety intake before importing messages')
    activated = dt.datetime.fromisoformat(activation[0])
    active = set(snapshot.get('active_reservation_ids', [])) | set(snapshot.get('upcoming_reservation_ids', []))
    alerts = []
    for conversation in snapshot['conversations']:
        reservation_id = conversation['reservation_id']
        if reservation_id not in active or len(conversation.get('property_ids', [])) != 1 or not conversation.get('conversation_id') or not conversation.get('source_messages_fingerprint'):
            continue
        check_in = dt.datetime.fromisoformat(conversation.get('effective_check_in', conversation['check_in']))
        check_out = dt.datetime.fromisoformat(conversation.get('effective_check_out', conversation['check_out']))
        if not check_in - dt.timedelta(days=2) <= current < check_out:
            continue
        for message in conversation['messages']:
            if message['sender_type'] != 'guest':
                continue
            created = dt.datetime.fromisoformat(message['created_at'])
            if not activated <= created <= current or not check_in - dt.timedelta(days=2) <= created < check_out:
                continue
            kind = detect(message['body'])
            if not kind:
                continue
            alert_id = hashlib.sha256((reservation_id + ':' + message['id']).encode()).hexdigest()
            body = {'property': conversation['property'], 'property_id': conversation['property_ids'][0], 'reservation_id': reservation_id, 'conversation_id': conversation['conversation_id'], 'source_messages_fingerprint': conversation['source_messages_fingerprint'], 'check_in': conversation['check_in'], 'check_out': conversation['check_out'], 'message_id': message['id'], 'message': message['body'], 'message_created_at': message['created_at'], 'kind': kind, 'city': conversation.get('city') or 'Charlotte'}
            cursor = db.execute("INSERT OR IGNORE INTO safety_alerts VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?)", (alert_id, reservation_id + ':safety', reservation_id, message['id'], conversation['property_ids'][0], kind, json.dumps(body), timestamp, timestamp, timestamp))
            if not cursor.rowcount:
                continue
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (reservation_id + ':safety', 'safety_alert_queued', timestamp, json.dumps({'alert_id': alert_id, 'kind': kind, 'message_id': message['id']})))
            logging.getLogger(__name__).warning(json.dumps({'event': 'safety_alert_queued', 'alert_id': alert_id, 'kind': kind}))
            alerts.append({'id': alert_id, 'issue_id': reservation_id + ':safety', **body})
    return alerts


def deliver(connect, stop):
    with connect() as db:
        timestamp = dt.datetime.now(dt.UTC).isoformat()
        rows = db.execute("SELECT id, issue_id FROM safety_alerts WHERE state = 'sending'").fetchall()
        for row in rows:
            db.execute("UPDATE safety_alerts SET state = 'unknown', error = 'The app restarted during the email attempt. Check Gmail before sending another notice.', updated_at = ? WHERE id = ? AND state = 'sending'", (timestamp, row['id']))
            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'safety_email_unknown', timestamp, json.dumps({'alert_id': row['id'], 'reason': 'restart'})))
    while not stop.is_set():
        try:
            with connect() as db:
                timestamp = dt.datetime.now(dt.UTC).isoformat()
                rows = db.execute("SELECT * FROM safety_alerts WHERE state = 'pending' AND next_attempt <= ? ORDER BY created_at LIMIT 20", (timestamp,)).fetchall()
            for row in rows:
                if stop.is_set():
                    return
                settings = email_configuration()
                settings['EMAIL_PROVIDER'] = 'gmail'
                if not email_ready(settings):
                    with connect() as db:
                        db.execute("UPDATE safety_alerts SET error = 'Configure Gmail and the manager email address to send this urgent notice.', next_attempt = ?, updated_at = ? WHERE id = ? AND state = 'pending'", ((dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)).isoformat(), timestamp, row['id']))
                    continue
                body = json.loads(row['body'])
                steps = guest_message(row['kind'], body['city'])
                location = ' '.join(body['property'].split())
                origin = public_origin()
                text = 'URGENT GUEST SAFETY REPORT\n\nProperty: ' + location + '\nReported at: ' + body['message_created_at'] + '\nReservation: ' + row['reservation_id'] + '\n\nGuest message:\n' + body['message'] + '\n\nEmergency guidance for this report:\n' + steps + '\n\nContact the guest immediately and coordinate the emergency response. Do not assume emergency services have been called.'
                if origin:
                    text += '\n\nManager dashboard: ' + origin
                payload = {'from': settings['NOTIFICATION_FROM'], 'to': [settings['MANAGER_EMAIL']], 'subject': 'URGENT: Guest safety report — ' + location, 'text': text}
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    claimed = db.execute("UPDATE safety_alerts SET state = 'sending', error = NULL, updated_at = ? WHERE id = ? AND state = 'pending'", (timestamp, row['id']))
                    if not claimed.rowcount:
                        continue
                    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'safety_email_attempted', timestamp, json.dumps({'alert_id': row['id']})))
                try:
                    result = send_email(settings, payload, 'safety:' + row['id'])
                except (OSError, smtplib.SMTPException, ValueError) as error:
                    state, email_id = 'unknown', None
                    failure = 'Email outcome is unknown. Check Gmail before sending another notice.'
                    logging.getLogger(__name__).error(json.dumps({'event': 'safety_email_unknown', 'alert_id': row['id'], 'error_type': type(error).__name__}))
                else:
                    state, email_id, failure = 'sent', result['id'], None
                with connect() as db:
                    updated = dt.datetime.now(dt.UTC).isoformat()
                    db.execute("UPDATE safety_alerts SET state = ?, email_id = ?, error = ?, updated_at = ? WHERE id = ? AND state = 'sending'", (state, email_id, failure, updated, row['id']))
                    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['issue_id'], 'safety_email_' + state, updated, json.dumps({'alert_id': row['id']})))
                logging.getLogger(__name__).warning(json.dumps({'event': 'safety_email_' + state, 'alert_id': row['id']}))
        except (sqlite3.Error, OSError, smtplib.SMTPException, ValueError, KeyError, TypeError) as error:
            logging.getLogger(__name__).error(json.dumps({'event': 'safety_worker_failed', 'error_type': type(error).__name__}))
            if stop.wait(15):
                return
        if stop.wait(2):
            return
