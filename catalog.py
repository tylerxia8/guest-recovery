import json
import re
import uuid
from urllib.parse import urlsplit

ITEMS = {
    'hand_towels': ('Hand towels', r'\bhand towels?\b', 'white cotton hand towels pack'),
    'bath_towels': ('Bath towels', r'(?<!hand )(?<!paper )\btowels?\b', 'white cotton bath towels'),
    'tv_remote': ('TV remote', r'\b(?:tv |television )?remotes?\b', ''),
    'laundry_pods': ('Laundry pods', r'\b(?:laundry|washing|detergent) (?:pods?|detergent)|\blaundry soap\b', 'laundry detergent pods'),
    'trash_bags': ('Trash bags', r'\b(?:trash|garbage|bin) (?:bags?|liners?)\b', 'trash bags'),
    'dish_pods': ('Dishwasher pods', r'\b(?:dish|dishwasher) (?:pods?|tablets?|detergent)\b', 'dishwasher detergent pods'),
    'toilet_paper': ('Toilet paper', r'\btoilet (?:paper|rolls?)\b', 'toilet paper small pack'),
    'soap': ('Hand soap', r'(?<!laundry )(?<!dish )(?<!dishwashing )(?<!bath )(?<!body )(?<!bar )\bsoap\b', 'hand soap'),
    'shampoo': ('Shampoo', r'\bshampoo\b', 'shampoo'),
    'blankets': ('Blankets', r'\bblankets?\b', 'washable blanket'),
    'pillows': ('Pillows', r'\bpillows?\b', 'bed pillow'),
    'batteries': ('Batteries', r'\bbatter(?:y|ies)\b', 'batteries'),
    'dish_soap': ('Dish soap', r'\b(?:dish|dishwashing) (?:soap|liquid)\b', 'Dawn Free Clear dishwashing liquid'),
    'paper_towels': ('Paper towels', r'\bpaper towels?\b', 'Bounty Select A Size paper towels small pack'),
    'bath_soap': ('Bath soap', r'\b(?:bath|body|bar) soap\b', 'Dove Sensitive Skin Beauty Bar'),
    'conditioner': ('Conditioner', r'(?<!air )\bconditioner\b', 'Dove Daily Moisture Conditioner'),
    'body_wash': ('Body wash', r'\bbody wash\b', 'Dove Ultra Sensitive Serum Body Wash'),
    'sponges': ('Dish sponges', r'\bsponges?\b', 'Scotch Brite Zero Scratch Scrub Sponges'),
    'rinse_aid': ('Rinse aid', r'\brinse aid\b', 'Finish Jet Dry 3 in 1 8.45 oz'),
    'coffee_filters': ('Coffee filters', r'\bcoffee filters?\b', 'Melitta 8 12 Cup Basket Filters 100'),
    'cooking_oil': ('Cooking oil', r'\b(?:cooking|olive) oil\b', 'Pompeian Smooth Extra Virgin Olive Oil'),
    'salt_pepper': ('Salt and pepper', r'\b(?:salt|pepper)\b', 'OXO Salt Pepper Grinder Set'),
    'pillowcases': ('Pillowcases', r'\bpillow\s?cases?\b', 'California Design Den 400 Thread Count White Pillowcases'),
    'sheets': ('Sheets', r'\b(?:bed ?sheets?|fitted sheets?)\b', 'California Design Den 400 Thread Count White Sheet Set'),
    'plunger': ('Toilet plunger', r'\bplungers?\b', 'OXO Good Grips Toilet Plunger Canister'),
    'hair_dryer': ('Hair dryer', r'\b(?:hair|blow) ?dryers?\b', 'Conair 303 Mid Size 1875 Watt Hair Dryer'),
    'hangers': ('Clothes hangers', r'\bhangers?\b', 'Amazon Basics Slim Velvet Hangers 30 Pack'),
    'iron': ('Clothes iron', r'\b(?:clothes iron|steam iron)\b', 'BLACK DECKER Easy Steam IR40V'),
    'ironing_board': ('Ironing board', r'\bironing boards?\b', 'Honey Can Do Tabletop Ironing Board Iron Rest'),
    'phone_charger': ('Phone charger', r'\b(?:phone|usb|usb-c) chargers?\b', 'Anker 511 Nano 3 30W'),
    'coffee_maker': ('Coffee maker', r'\bcoffee ?makers?\b', 'Hamilton Beach 46292 Coffee Maker'),
    'toaster': ('Toaster', r'\btoasters?\b', 'Hamilton Beach 22614V Toaster'),
    'high_chair': ('High chair', r'\bhigh ?chairs?\b', 'Graco Slim Snacker Highchair'),
    'travel_crib': ('Travel crib or playard', r'\b(?:travel cribs?|playards?|pack.?n.?play)\b', 'Graco Pack n Play Portable Playard'),
}

DEFAULTS = {
    'trash_bags': ('Glad ForceFlex fragrance-free 13-gallon drawstring trash bags', 'Confirm bin size; custom-fit bins may require specific liners.'),
    'laundry_pods': ('Tide PODS Free & Gentle', 'Confirm the washer permits pods; choose a practical pack for the stay.'),
    'dish_pods': ('Cascade Free & Clear ActionPacs', 'Dishwasher detergent only; keep separate from laundry pods.'),
    'dish_soap': ('Dawn Free & Clear dishwashing liquid', 'For handwashing dishes; Free & Clear does not necessarily mean fragrance-free.'),
    'toilet_paper': ('Charmin Ultra Strong 6 Mega Rolls', 'Confirm larger rolls fit the holder.'),
    'paper_towels': ('Bounty Select-A-Size paper towels small multipack', 'Choose enough for the stay without unnecessary bulk.'),
    'bath_soap': ('Dove Sensitive Skin Beauty Bar', 'Choose individually wrapped bars for guest use.'),
    'batteries': ('Duracell Coppertop alkaline batteries', 'Confirm AA or AAA before choosing a pack.'),
    'bath_towels': ('American Soft Linen white cotton bath towels 4-pack 27 x 54 inches', 'Match existing towels and confirm pack contents.'),
    'hand_towels': ('American Soft Linen white Turkish cotton hand towels 4-pack', 'Match existing color and confirm pack contents.'),
    'shampoo': ('Dove Daily Moisture Shampoo', 'Match the existing dispenser or use a sealed bottle.'),
    'conditioner': ('Dove Daily Moisture Conditioner', 'Match the shampoo and existing dispenser.'),
    'body_wash': ('Dove Ultra Sensitive Serum Body Wash 18.5 oz', 'Choose Ultra Sensitive for the fragrance-free formula; check the exact label.'),
    'sponges': ('Scotch-Brite Zero Scratch Scrub Sponges', 'Choose a small multipack and follow surface-care instructions.'),
    'rinse_aid': ('Finish Jet-Dry 3-in-1 8.45 oz', 'Confirm the dishwasher has a rinse-aid compartment.'),
    'coffee_filters': ('Melitta 8-12 Cup Basket Filters 100-count', 'Basket brewer only; confirm the coffee maker before selecting.'),
    'cooking_oil': ('Pompeian Smooth Extra Virgin Olive Oil small bottle', 'Choose a sealed bottle and avoid unnecessary bulk.'),
    'salt_pepper': ('OXO Salt & Pepper Grinder Set', 'Replace missing grinders; replenish existing ones instead when possible.'),
    'blankets': ('Bedsure 300 GSM Fleece Blanket', 'Match bed size and property color.'),
    'pillows': ('Beckham Hotel Collection Original Down-Alternative Pillows 2-pack', 'Match Standard/Queen or King pillowcases.'),
    'pillowcases': ('California Design Den 400-thread-count cotton pillowcases white', 'Match pillow size and existing linen style.'),
    'sheets': ('California Design Den 400-thread-count cotton sheet set white', 'Confirm bed size and mattress depth.'),
    'plunger': ('OXO Good Grips Toilet Plunger and Canister', 'Includes enclosed storage and a drip tray.'),
    'hair_dryer': ('Conair Mid-Size 1875-Watt Dryer 303 series', 'Choose a US model and plug.'),
    'hangers': ('Amazon Basics Slim Velvet Hangers 30-pack', 'Match existing hangers and avoid unnecessary quantities.'),
    'iron': ('BLACK+DECKER Easy Steam IR40V', 'Select the version with automatic shutoff.'),
    'ironing_board': ('Honey-Can-Do Tabletop Ironing Board with Iron Rest', 'Confirm a suitable tabletop and storage space.'),
    'phone_charger': ('Anker 511 Nano 3 30W', 'Choose a US plug and add the correct USB-C or Lightning cable; cable is separate.'),
    'coffee_maker': ('Hamilton Beach 12-Cup Programmable Coffee Maker 46292', 'Confirm the property uses ground coffee rather than pods.'),
    'toaster': ('Hamilton Beach 2-Slice Metal Toaster 22614V', 'Confirm two slots meet the property needs.'),
    'high_chair': ('Graco Slim Snacker Highchair', 'Manager review required: exact model, current recall status, suitability, and assembly.'),
    'travel_crib': ('Graco Pack n Play Portable Playard', 'Manager review required: exact model, current recall status, suitability, and manufacturer instructions.'),
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
