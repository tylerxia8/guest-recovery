import json
import math
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def profile(db, property_id):
    row = db.execute('SELECT * FROM fulfillment_profiles WHERE property_id = ?', (property_id,)).fetchone()
    imported = json.loads(row['imported_address']) if row else {}
    saved = json.loads(row['body']) if row and row['body'] else None
    overridden = bool(saved and all(saved['source_address'].get(key, '') == imported.get(key, '') for key in ('line1', 'line2', 'city', 'region', 'postal_code', 'country', 'timezone')))
    address = saved['address'] if overridden else {key: imported.get(key, '') for key in ('line1', 'line2', 'city', 'region', 'postal_code', 'country', 'timezone')}
    verified = all(isinstance(address.get(key), str) and address[key].strip() for key in ('line1', 'city', 'region', 'postal_code', 'country', 'timezone'))
    if verified:
        try:
            ZoneInfo(address['timezone'])
        except (ValueError, ZoneInfoNotFoundError):
            verified = False
        verified = verified and bool(re.fullmatch(r'[A-Z]{2}', address['country']))
        if address['country'] == 'US':
            verified = verified and bool(re.fullmatch(r'[0-9]{5}(?:-[0-9]{4})?', address['postal_code']))
    home = imported.get('home', {})
    quantity = 2
    return {'property_id': property_id, 'imported_address': imported, 'address': address, 'previous_address': saved['address'] if saved and not overridden else None, 'instructions': saved['instructions'] if overridden else imported.get('instructions', ''), 'hand_towel_quantity': quantity, 'home': home, 'source': 'manager' if overridden else 'hospitable', 'version': row['version'] if row else 0, 'verified': bool(verified), 'verified_at': saved['verified_at'] if overridden else None}


def quantities(issue, destination):
    from catalog import ITEMS
    home = destination.get('home', {}) if destination else {}
    bathrooms = max(1, math.ceil(home.get('bathrooms') or 1))
    if any(re.search(r'\b(?:in (?:the |our )?(?:bathroom|kitchen)|(?:master|main|upstairs|downstairs|guest) bathroom)\b', message['body'], re.IGNORECASE) for message in issue['evidence']):
        bathrooms = 1
    defaults = {'hand_towels': (2, 'towels'), 'bath_towels': (2, 'towels'), 'trash_bags': (2, 'bags'), 'toilet_paper': (min(20, 2 * bathrooms), 'rolls'), 'paper_towels': (2, 'rolls'), 'laundry_pods': (2, 'pods'), 'dish_pods': (2, 'pods'), 'soap': (bathrooms, 'bottles'), 'shampoo': (1, 'bottle'), 'conditioner': (1, 'bottle'), 'body_wash': (bathrooms, 'bottles'), 'bath_soap': (bathrooms, 'bars'), 'blankets': (1, 'blanket'), 'pillows': (1, 'pillow'), 'batteries': (4, 'batteries'), 'sponges': (2, 'sponges'), 'salt_pepper': (1, 'small salt-and-pepper set'), 'cooking_oil': (1, 'small bottle')}
    result = []
    for kind in issue['item_types']:
        count, unit = defaults.get(kind, (1, 'item'))
        source = 'Home-size default' if kind in ('toilet_paper', 'soap', 'body_wash', 'bath_soap') else 'Starter replenishment'
        for message in issue['evidence']:
            pattern = r'\b(?:need|bring|send|provide|request|can we have|could we have|can we get|could we get)\s+(?:us\s+)?([1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:(?:extra|additional|more)\s+)?(?:' + ITEMS[kind][1] + ')'
            match = re.search(pattern, message['body'].replace('\u2019', "'"), re.IGNORECASE)
            if match:
                number = match[1].lower()
                count = int(number) if number.isdigit() else ('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten').index(number) + 1
                source = 'Guest request'
        result.append({'item_type': kind, 'quantity': count, 'unit': unit, 'basis': source})
    return result



def save(db, data, timestamp):
    identifier = str(uuid.UUID(data.get('property_id', '')))
    if not db.execute('SELECT id FROM rental_units WHERE id = ?', (identifier,)).fetchone():
        raise ValueError('Choose an imported rental unit')
    current = profile(db, identifier)
    if type(data.get('version')) is not int or data['version'] != current['version']:
        raise ValueError('Property details changed; reload before confirming')
    raw = data.get('address')
    if not isinstance(raw, dict) or not raw:
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
    quantity = data.get('hand_towel_quantity', 2)
    if not isinstance(instructions, str) or len(instructions) > 2000:
        raise ValueError('Delivery instructions must be at most 2000 characters')
    if type(quantity) is not int or not 1 <= quantity <= 20:
        raise ValueError('Default hand-towel quantity must be between 1 and 20 towels')
    if data.get('confirmed') is not True:
        raise ValueError('Confirm the full delivery address, unit number, and instructions')
    body = {'address': address, 'instructions': instructions.strip(), 'hand_towel_quantity': quantity, 'source_address': current['imported_address'], 'verified_at': timestamp}
    db.execute('INSERT INTO fulfillment_profiles VALUES (?, ?, ?, ?) ON CONFLICT(property_id) DO UPDATE SET body = excluded.body, version = excluded.version', (identifier, json.dumps(current['imported_address']), json.dumps(body), current['version'] + 1))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, 'fulfillment_profile_confirmed', timestamp, json.dumps({'version': current['version'] + 1, 'hand_towel_quantity': quantity})))
    return {'property_id': identifier}
