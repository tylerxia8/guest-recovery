import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing

from guest_app import DATABASE, ROOT, audit, connect, initialize, mutate


def get(path, token):
    request = urllib.request.Request('https://public.api.hospitable.com/v2/' + path, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            if not isinstance(result, dict) or 'data' not in result:
                raise ValueError('Hospitable returned an unexpected response')
            return result['data']
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Hospitable request failed with HTTP ' + str(error.code)) from None
            delay = 30 if error.code == 429 else 2 ** attempt
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ValueError('Hospitable request timed out or could not connect') from None
            delay = 2 ** attempt
        time.sleep(delay)
    raise RuntimeError('Retry limit reached')


def sync(identifier):
    token = os.environ.get('HOSPITABLE_TOKEN', '')
    if not token:
        raise ValueError('Set HOSPITABLE_TOKEN in the server environment')
    reservation = get('reservations/' + urllib.parse.quote(identifier, safe='') + '?include=guest,properties', token)
    reservation_id = str(uuid.UUID(reservation['id']))
    if reservation['reservation_status']['current']['category'] != 'accepted':
        raise ValueError('Only accepted reservations can be imported')
    check_in = dt.datetime.fromisoformat(reservation['check_in'])
    check_out = dt.datetime.fromisoformat(reservation['check_out'])
    if check_in.tzinfo is None or check_out.tzinfo is None:
        raise ValueError('Reservation dates must include timezones')
    if not check_in <= dt.datetime.now(dt.UTC) < check_out:
        raise ValueError('Reservation is not currently in-house')
    properties = reservation['properties']
    if len(properties) != 1:
        raise ValueError('Reservation must have exactly one property for automatic import')
    property_id = str(uuid.UUID(properties[0]['id']))
    messages = get('reservations/' + reservation_id + '/messages', token)
    if not isinstance(messages, list):
        raise TypeError('Hospitable messages must be a list')
    imported = 0
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for message in messages:
            if message['sender_type'] != 'guest' or message['content_type'] != 'text/plain':
                continue
            timestamp = dt.datetime.fromisoformat(message['created_at'])
            if timestamp.tzinfo is None:
                raise ValueError('Message timestamp must include timezone')
            if timestamp < check_in or timestamp >= check_out:
                continue
            if message.get('reservation_id') not in (None, reservation_id):
                raise ValueError('Message belongs to a different reservation')
            if message['conversation_id'] != reservation['conversation_id']:
                raise ValueError('Message conversation does not match reservation')
            source_id = message['platform_id']
            if not isinstance(source_id, (str, int)) or isinstance(source_id, bool) or not str(source_id):
                raise ValueError('Message is missing a stable platform ID')
            data = {'message_id': ':'.join((reservation_id, message['platform'], str(source_id))), 'reservation_id': reservation_id, 'property_id': property_id, 'message': message['body'], 'sender_type': 'guest'}
            result = mutate(db, '/api/intake', data)
            if not result['duplicate']:
                audit(db, result['incident_id'], 'hospitable_import', {'check_in': reservation['check_in'], 'check_out': reservation['check_out']})
                imported += 1
    print(json.dumps({'event': 'hospitable_sync', 'imported_messages': imported}))


def research():
    token = os.environ.get('HOSPITABLE_TOKEN', '')
    if not token:
        for line in (ROOT / '.env').read_text().splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'HOSPITABLE_TOKEN':
                token = value.strip().strip('\"\'')
    if not token:
        raise ValueError('HOSPITABLE_TOKEN is empty')
    properties = []
    for page in range(1, 101):
        rows = get('properties?' + urllib.parse.urlencode({'per_page': 100, 'page': page}), token)
        properties.extend(rows)
        if len(rows) < 100:
            break
    else:
        raise ValueError('Property pagination limit reached; previous snapshot preserved')
    current = dt.datetime.now(dt.UTC)
    end = current.date() + dt.timedelta(days=365 * 2)
    params = [('properties[]', item['id']) for item in properties] + [('status[]', 'accepted'), ('date_query', 'checkout'), ('start_date', str(current.date() - dt.timedelta(days=1))), ('end_date', str(end)), ('include', 'properties'), ('per_page', 100)]
    reservations = []
    if properties:
        for page in range(1, 101):
            rows = get('reservations?' + urllib.parse.urlencode(params + [('page', page)]), token)
            for reservation in rows:
                check_in = dt.datetime.fromisoformat(reservation['check_in'])
                from stay_adjustments import effective_stay
                candidate = {'reservation_id': str(reservation['id']), 'property_ids': [str(item['id']) for item in reservation['properties']], 'check_in': reservation['check_in'], 'check_out': reservation['check_out']}
                with connect() as db:
                    bounds = effective_stay(db, candidate)
                if dt.datetime.fromisoformat(bounds['effective_check_out']) > current and check_in <= current + dt.timedelta(days=2) and reservation['reservation_status']['current']['category'] == 'accepted':
                    reservations.append(reservation)
            if len(rows) < 100:
                break
        else:
            raise ValueError('Reservation pagination limit reached; previous snapshot preserved')
    conversations = []
    nontext = 0
    for reservation in reservations:
        identifier = str(uuid.UUID(reservation['id']))
        rows = get('reservations/' + identifier + '/messages', token)
        messages = []
        for message in rows:
            if message.get('reservation_id') not in (None, identifier) or message['conversation_id'] != reservation['conversation_id']:
                raise ValueError('Message does not match its reservation')
            body = message.get('body')
            if body is None:
                nontext += 1
                continue
            if not isinstance(body, str):
                raise TypeError('Unexpected message body; previous snapshot preserved')
            timestamp = dt.datetime.fromisoformat(message['created_at'])
            if timestamp.tzinfo is None:
                raise ValueError('Message timestamp requires timezone')
            messages.append({'id': str(message['id']), 'sender_type': message['sender_type'], 'source': message.get('source'), 'sender': message.get('sender'), 'body': body, 'created_at': timestamp.astimezone(dt.UTC).isoformat()})
        conversations.append({'reservation_id': identifier, 'property': ', '.join(item['name'] for item in reservation['properties']), 'property_ids': [str(uuid.UUID(item['id'])) for item in reservation['properties']], 'check_in': reservation['check_in'], 'check_out': reservation['check_out'], 'messages': messages, 'conversation_id': reservation['conversation_id'], 'source_messages_fingerprint': hashlib.sha256(json.dumps(sorted(rows, key=lambda row: str(row['id'])), sort_keys=True).encode()).hexdigest()})
        from guest_replies import queue_safety_replies
        from safety import ingest as ingest_safety
        conversation = conversations[-1]
        with connect() as db:
            conversation.update(effective_stay(db, conversation))
        property_data = next((item for item in properties if len(conversation['property_ids']) == 1 and item['id'] == conversation['property_ids'][0]), None)
        city = ((property_data or {}).get('address') or {}).get('city') or 'Charlotte'
        if not isinstance(city, str) or len(city) > 1000:
            raise TypeError('Unexpected property city')
        conversation['city'] = city
        safety_now = dt.datetime.now(dt.UTC).isoformat()
        safety_snapshot = {'synced_at': safety_now, 'active_reservation_ids': [identifier], 'upcoming_reservation_ids': [identifier], 'conversations': [conversation]}
        with connect() as db:
            ingest_safety(db, safety_snapshot, safety_now)
        try:
            with connect() as db:
                alerts = [dict(row) for row in db.execute('SELECT reservation_id, message_id FROM safety_alerts WHERE reservation_id = ? AND created_at >= ?', (identifier, (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)).isoformat()))]
                queue_safety_replies(db, alerts, safety_snapshot, safety_now)
        except (sqlite3.Error, ValueError) as error:
            logging.getLogger(__name__).error(json.dumps({'event': 'safety_guest_queue_failed', 'reservation_id': identifier, 'error_type': type(error).__name__}))
        from stay_adjustments import ingest as ingest_stay_requests
        stay_snapshot = {**safety_snapshot, 'upcoming_reservation_ids': [identifier]}
        with connect() as db:
            ingest_stay_requests(db, stay_snapshot, safety_now)
        print(json.dumps({'event': 'research_progress', 'conversations': len(conversations), 'total': len(reservations)}), flush=True)
    with connect() as db:
        previous = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    retained = {item['reservation_id']: item for item in json.loads(previous['body'])['conversations']} if previous else {}
    retained.update({item['reservation_id']: item for item in conversations})
    for conversation in retained.values():
        if 'property_ids' not in conversation:
            matches = [item['id'] for item in properties if item['name'] == conversation['property']]
            if len(matches) == 1:
                conversation['property_ids'] = [str(uuid.UUID(matches[0]))]
    snapshot = {'synced_at': dt.datetime.now(dt.UTC).isoformat(), 'property_count': len(properties), 'conversations': list(retained.values()), 'active_reservation_count': sum(dt.datetime.fromisoformat(item['effective_check_in']) <= current < dt.datetime.fromisoformat(item['effective_check_out']) for item in conversations), 'nontext_message_count': nontext, 'scope': 'Accepted current stays and arrivals within 48 hours are refreshed from Hospitable every five minutes after each completed import. Approved stay times are coordinated with the cleaning scheduler. Earlier imported conversations are retained as practice scenarios. Full returned conversations; text only. Rule-confirmed stay replies and qualifying emergency guidance are automatic. Other guest replies require review.'}
    snapshot['active_reservation_ids'] = [item['reservation_id'] for item in conversations if dt.datetime.fromisoformat(item['effective_check_in']) <= current < dt.datetime.fromisoformat(item['effective_check_out'])]
    snapshot['upcoming_reservation_ids'] = [item['reservation_id'] for item in conversations if current < dt.datetime.fromisoformat(item['effective_check_in'])]
    with connect() as db:
        for item in properties:
            db.execute('INSERT INTO rental_units VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name', (str(uuid.UUID(item['id'])), item['name']))
            raw_address = item.get('address') or {}
            if not isinstance(raw_address, dict):
                raise TypeError('Unexpected property address format')
            fields = {}
            for field in ('number', 'street', 'city', 'state', 'postcode', 'country'):
                value = raw_address.get(field)
                if value is None:
                    value = ''
                if field == 'number' and type(value) is int:
                    value = str(value)
                if not isinstance(value, str) or len(value) > 1000:
                    raise ValueError('Unexpected property address field')
                fields[field] = value.strip()
            timezone = item.get('timezone') or ''
            if not isinstance(timezone, str) or len(timezone) > 100:
                raise ValueError('Unexpected property timezone')
            imported_address = {'line1': ' '.join(value for value in (fields['number'], fields['street']) if value), 'line2': '', 'city': fields['city'], 'region': fields['state'], 'postal_code': fields['postcode'], 'country': fields['country'].upper(), 'timezone': timezone}
            capacity = item.get('capacity') or {}
            if not isinstance(capacity, dict):
                raise TypeError('Unexpected property capacity format')
            home = {}
            for field, source in (('bedrooms', 'bedrooms'), ('bathrooms', 'bathrooms'), ('guests', 'max')):
                value = capacity.get(source)
                if value is not None:
                    if type(value) not in (int, float) or not 0 <= value <= 100:
                        raise ValueError('Unexpected property capacity field')
                    home[field] = value
            imported_address['home'] = home
            db.execute('INSERT INTO fulfillment_profiles VALUES (?, ?, NULL, 0) ON CONFLICT(property_id) DO UPDATE SET imported_address = excluded.imported_address, version = fulfillment_profiles.version + 1 WHERE fulfillment_profiles.imported_address != excluded.imported_address', (str(uuid.UUID(item['id'])), json.dumps(imported_address, sort_keys=True)))
        db.execute('INSERT INTO research_snapshot VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body', (json.dumps(snapshot),))
    from cleaner_returns import queue_ready_visits
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            queue_ready_visits(db, snapshot, dt.datetime.now(dt.UTC).isoformat())
    except (sqlite3.Error, ValueError) as error:
        logging.getLogger(__name__).warning(json.dumps({
            'event': 'automatic_cleaning_visit_not_queued', 'error_type': type(error).__name__,
        }))
    from reply_library import learn
    with connect() as db:
        learn(db, conversations)
    print(json.dumps({'event': 'research_complete', 'conversations': len(conversations)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('research')
    sync_parser = commands.add_parser('sync')
    sync_parser.add_argument('reservation')
    backup_parser = commands.add_parser('backup')
    backup_parser.add_argument('destination')
    args = parser.parse_args()
    os.umask(0o077)
    initialize()
    try:
        if args.command == 'research':
            research()
        elif args.command == 'sync':
            sync(args.reservation)
        else:
            destination = os.path.abspath(args.destination)
            if destination == os.path.abspath(DATABASE) or os.path.exists(destination):
                raise ValueError('Backup destination must be a new file')
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with connect() as source, closing(sqlite3.connect(destination, timeout=5)) as target:
                deadline = time.monotonic() + 60

                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise TimeoutError('Database backup timed out')

                source.backup(target, pages=100, sleep=0.1, progress=progress)
            print(json.dumps({'event': 'backup_complete'}))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
