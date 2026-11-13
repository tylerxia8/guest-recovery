import argparse
from contextlib import closing
import datetime as dt
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from app import DATABASE, audit, connect, initialize, mutate


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
    check_in = dt.datetime.fromisoformat(reservation['check_in'].replace('Z', '+00:00'))
    check_out = dt.datetime.fromisoformat(reservation['check_out'].replace('Z', '+00:00'))
    if check_in.tzinfo is None or check_out.tzinfo is None:
        raise ValueError('Reservation dates must include timezones')
    if not check_in <= dt.datetime.now(dt.timezone.utc) < check_out:
        raise ValueError('Reservation is not currently in-house')
    properties = reservation['properties']
    if len(properties) != 1:
        raise ValueError('Reservation must have exactly one property for automatic import')
    property_id = str(uuid.UUID(properties[0]['id']))
    messages = get('reservations/' + reservation_id + '/messages', token)
    if not isinstance(messages, list):
        raise ValueError('Hospitable messages must be a list')
    imported = 0
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for message in messages:
            if message['sender_type'] != 'guest' or message['content_type'] != 'text/plain':
                continue
            timestamp = dt.datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    sync_parser = commands.add_parser('sync')
    sync_parser.add_argument('reservation')
    backup_parser = commands.add_parser('backup')
    backup_parser.add_argument('destination')
    args = parser.parse_args()
    os.umask(0o077)
    initialize()
    try:
        if args.command == 'sync':
            sync(args.reservation)
        else:
            destination = os.path.abspath(args.destination)
            if destination == os.path.abspath(DATABASE) or os.path.exists(destination):
                raise ValueError('Backup destination must be a new file')
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with connect() as source, closing(sqlite3.connect(destination, timeout=5)) as target:
                source.backup(target, pages=100, sleep=0.1)
            print(json.dumps({'event': 'backup_complete'}))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
