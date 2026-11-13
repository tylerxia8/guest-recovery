import json
import re
import uuid
from urllib.parse import urlsplit

ITEMS = {
    'hand_towels': ('Hand towels', r'\bhand towels?\b', 'white cotton hand towels pack'),
    'bath_towels': ('Bath towels', r'(?<!hand )\btowels?\b', 'white cotton bath towels'),
    'tv_remote': ('TV remote', r'\b(?:tv |television )?remotes?\b', ''),
    'laundry_pods': ('Laundry pods', r'\b(?:laundry|washing|detergent) (?:pods?|detergent)|\blaundry soap\b', 'laundry detergent pods'),
    'trash_bags': ('Trash bags', r'\b(?:trash|garbage|bin) (?:bags?|liners?)\b', 'trash bags'),
    'dish_pods': ('Dishwasher pods', r'\b(?:dish|dishwasher) (?:pods?|tablets?|detergent)\b', 'dishwasher detergent pods'),
    'toilet_paper': ('Toilet paper', r'\btoilet (?:paper|rolls?)\b', 'toilet paper small pack'),
    'soap': ('Hand soap', r'(?<!laundry )(?<!dish )\bsoap\b', 'hand soap'),
    'shampoo': ('Shampoo', r'\bshampoo\b', 'shampoo'),
    'blankets': ('Blankets', r'\bblankets?\b', 'washable blanket'),
    'pillows': ('Pillows', r'\bpillows?\b', 'bed pillow'),
    'batteries': ('Batteries', r'\bbatter(?:y|ies)\b', 'batteries'),
}


def save(db, data, timestamp):
    property_id = str(uuid.UUID(data.get('property_id', '')))
    if not db.execute('SELECT id FROM rental_units WHERE id = ?', (property_id,)).fetchone():
        raise ValueError('Choose a unit imported from Hospitable')
    kind = data.get('item_type')
    if not isinstance(kind, str) or kind not in ITEMS:
        raise ValueError('Choose a supported item type')
    fields = {}
    for key, maximum in [('title', 300), ('room', 100), ('tv_brand', 100), ('tv_model', 100), ('amazon_product', 2000)]:
        value = data.get(key)
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError('Invalid ' + key)
        fields[key] = value.strip()
    if not fields['title']:
        raise ValueError('Enter a product name')
    product = fields['amazon_product']
    if re.fullmatch(r'[A-Za-z0-9]{10}', product):
        asin = product.upper()
    else:
        url = urlsplit(product)
        if url.scheme != 'https' or url.hostname not in ('amazon.com', 'www.amazon.com', 'smile.amazon.com') or url.port not in (None, 443) or url.username or url.password:
            raise ValueError('Use an Amazon.com product URL or a 10-character ASIN')
        match = re.search(r'/(?:dp|gp/product)/([A-Za-z0-9]{10})(?:/|$)', url.path)
        if not match:
            raise ValueError('Use the full Amazon product link, not a search or shortened link')
        asin = match[1].upper()
    fields['room'] = ' '.join(fields['room'].split())
    if kind == 'tv_remote':
        if not all(fields[key] for key in ('room', 'tv_brand', 'tv_model')):
            raise ValueError('TV remotes need a room, TV brand, and exact TV model')
    else:
        fields['room'] = fields['tv_brand'] = fields['tv_model'] = ''
    version = data.get('version')
    if type(version) is not int or version < 0:
        raise ValueError('Invalid product version')
    identifier = data.get('id')
    if identifier:
        identifier = str(uuid.UUID(identifier))
        previous = db.execute('SELECT * FROM preferred_products WHERE id = ? AND active = 1', (identifier,)).fetchone()
        if not previous or previous['property_id'] != property_id or previous['version'] != version:
            raise ValueError('Product changed; reload before editing')
    elif version != 0:
        raise ValueError('New products must have version zero')
    duplicate = db.execute('SELECT id FROM preferred_products WHERE property_id = ? AND item_type = ? AND lower(room) = lower(?) AND active = 1', (property_id, kind, fields['room'])).fetchone()
    if duplicate and duplicate['id'] != identifier:
        raise ValueError('This unit already has a preferred product for that item and room. Edit it instead.')
    identifier = identifier or str(uuid.uuid4())
    db.execute('INSERT INTO preferred_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(id) DO UPDATE SET item_type = excluded.item_type, room = excluded.room, tv_brand = excluded.tv_brand, tv_model = excluded.tv_model, title = excluded.title, asin = excluded.asin, version = excluded.version, updated_at = excluded.updated_at', (identifier, property_id, kind, fields['room'], fields['tv_brand'], fields['tv_model'], fields['title'], asin, version + 1, timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (property_id, 'preferred_product_saved', timestamp, json.dumps({'id': identifier, 'asin': asin, 'version': version + 1})))
    return {'id': identifier}
