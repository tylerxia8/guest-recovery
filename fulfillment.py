import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def profile(db, property_id):
    row = db.execute('SELECT * FROM fulfillment_profiles WHERE property_id = ?', (property_id,)).fetchone()
    imported = json.loads(row['imported_address']) if row else {}
    saved = json.loads(row['body']) if row and row['body'] else None
    verified = bool(saved and saved['source_address'] == imported)
    address = saved['address'] if verified else imported
    return {'property_id': property_id, 'imported_address': imported, 'address': address, 'previous_address': saved['address'] if saved and not verified else None, 'instructions': saved['instructions'] if saved else '', 'hand_towel_quantity': saved['hand_towel_quantity'] if saved else 4, 'version': row['version'] if row else 0, 'verified': verified, 'verified_at': saved['verified_at'] if verified else None}


def save(db, data, timestamp):
    identifier = str(uuid.UUID(data.get('property_id', '')))
    if not db.execute('SELECT id FROM rental_units WHERE id = ?', (identifier,)).fetchone():
        raise ValueError('Choose an imported rental unit')
    current = profile(db, identifier)
    if type(data.get('version')) is not int or data['version'] != current['version']:
        raise ValueError('Property details changed; reload before confirming')
    raw = data.get('address')
    if not isinstance(raw, dict):
        raise ValueError('Enter a delivery address')
    address = {}
    for field, maximum in (('line1', 300), ('line2', 100), ('city', 100), ('region', 100), ('postal_code', 32), ('country', 2), ('timezone', 100)):
        value = raw.get(field)
        if not isinstance(value, str) or len(value) > maximum or (field != 'line2' and not value.strip()):
            raise ValueError('Invalid delivery ' + field)
        address[field] = value.strip()
    if not re.fullmatch(r'[A-Z]{2}', address['country']):
        raise ValueError('Use a two-letter uppercase country code')
    if address['country'] == 'US' and not re.fullmatch(r'[0-9]{5}(?:-[0-9]{4})?', address['postal_code']):
        raise ValueError('Enter a valid US ZIP code')
    try:
        ZoneInfo(address['timezone'])
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('Choose a valid property time zone') from None
    instructions = data.get('instructions')
    quantity = data.get('hand_towel_quantity')
    if not isinstance(instructions, str) or len(instructions) > 2000:
        raise ValueError('Delivery instructions must be at most 2000 characters')
    if type(quantity) is not int or not 1 <= quantity <= 20:
        raise ValueError('Default hand-towel quantity must be between 1 and 20 towels')
    if data.get('confirmed') is not True:
        raise ValueError('Confirm the full delivery address, unit number, instructions, and towel quantity')
    body = {'address': address, 'instructions': instructions.strip(), 'hand_towel_quantity': quantity, 'source_address': current['imported_address'], 'verified_at': timestamp}
    db.execute('INSERT INTO fulfillment_profiles VALUES (?, ?, ?, ?) ON CONFLICT(property_id) DO UPDATE SET body = excluded.body, version = excluded.version', (identifier, json.dumps(current['imported_address']), json.dumps(body), current['version'] + 1))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, 'fulfillment_profile_confirmed', timestamp, json.dumps({'version': current['version'] + 1, 'hand_towel_quantity': quantity})))
    return {'property_id': identifier}
