import datetime as dt
import hashlib
import json
import re

from local_supply import change, recheck_live


def respond(db, data, timestamp, path):
    submit = path.endswith('/reply')
    token = data.get('token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise ValueError('This response link is invalid.')
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    link = db.execute('SELECT * FROM cleaner_response_links WHERE token_hash = ?', (token_hash,)).fetchone()
    if not link:
        raise ValueError('This response link is invalid.')
    row = db.execute('SELECT * FROM local_requests WHERE id = ?', (link['request_id'],)).fetchone()
    details = json.loads(row['body'])
    if details.get('mode') != 'live':
        raise ValueError('This link cannot update a simulated request.')
    if link['fingerprint']:
        if submit:
            fingerprint = hashlib.sha256(json.dumps({key: value for key, value in data.items() if key != 'token'}, sort_keys=True).encode()).hexdigest()
            if fingerprint != link['fingerprint']:
                raise ValueError('A reply has already been recorded. Contact the manager to change it.')
        return {'recorded': True, 'status': 'Your reply was recorded. The manager must arrange delivery.'}
    current = dt.datetime.fromisoformat(timestamp)
    if row['state'] != 'pending' or row['notice_status'] != 'sent' or current >= dt.datetime.fromisoformat(details['reply_by']):
        raise ValueError('This request is no longer awaiting a reply. Please contact the property manager.')
    recheck_live(db, details, row['issue_id'], timestamp)
    if not submit:
        from catalog import ITEMS
        return {'recorded': False, 'property': details['property'], 'items': [{'label': ITEMS[item][0], **next((quantity for quantity in details.get('supply_quantities', []) if quantity['item_type'] == item), {})} for item in details['item_types']], 'needed_by': details['needed_by'], 'reply_by': details['reply_by'], 'timezone': details.get('timezone', 'America/New_York')}
    action = data.get('action')
    if action not in ('available', 'unavailable'):
        raise ValueError('Choose whether you can supply all the items in time.')
    charge = data.get('charge_cents')
    if action == 'available' and (type(charge) is not int or not 0 <= charge <= 1000000):
        raise ValueError('Enter the total proposed charge, including delivery, or zero if free.')
    change(db, '/api/local-requests/' + row['id'] + '/' + action, {'version': row['version'], 'arrival': data.get('arrival'), 'items_confirmed': data.get('items_confirmed')}, timestamp)
    updated = json.loads(db.execute('SELECT body FROM local_requests WHERE id = ?', (row['id'],)).fetchone()['body'])
    updated['reply_source'] = 'cleaner_link'
    if action == 'available':
        updated['charge_cents'] = charge
    db.execute('UPDATE local_requests SET body = ? WHERE id = ?', (json.dumps(updated), row['id']))
    fingerprint = hashlib.sha256(json.dumps({key: value for key, value in data.items() if key != 'token'}, sort_keys=True).encode()).hexdigest()
    db.execute('UPDATE cleaner_response_links SET fingerprint = ? WHERE request_id = ?', (fingerprint, row['id']))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'cleaner_link_reply', timestamp, json.dumps({'decision': action, 'charge_cents': charge if action == 'available' else None, 'actor': 'cleaner_response_link'})))
    return {'recorded': True, 'status': 'Your availability was recorded. Wait for the manager to arrange delivery.' if action == 'available' else 'Your reply was recorded. The manager can use Amazon instead.'}
