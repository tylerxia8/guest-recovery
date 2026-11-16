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
    'shampoo': ('Shampoo', r'\bshampoos?\b', 'shampoo'),
    'blankets': ('Blankets', r'\bblankets?\b', 'washable blanket'),
    'pillows': ('Pillows', r'\bpillows?\b', 'bed pillow'),
    'batteries': ('Batteries', r'\bbatter(?:y|ies)\b', 'batteries'),
    'dish_soap': ('Dish soap', r'\b(?:dish|dishwashing) (?:soap|liquid)\b', 'Dawn Free Clear dishwashing liquid'),
    'paper_towels': ('Paper towels', r'\bpaper towels?\b', 'Bounty Select A Size paper towels small pack'),
    'bath_soap': ('Bath soap', r'\b(?:bath|body|bar) soap\b', 'Dove Sensitive Skin Beauty Bar'),
    'conditioner': ('Conditioner', r'(?<!air )\bconditioners?\b', 'travel size conditioner single bottle'),
    'body_wash': ('Body wash', r'\bbody wash\b', 'Dove Ultra Sensitive Serum Body Wash'),
    'sponges': ('Dish sponges', r'\bsponges?\b', 'Scotch Brite Zero Scratch Scrub Sponges'),
    'rinse_aid': ('Rinse aid', r'\brinse aid\b', 'Finish Jet Dry 3 in 1 8.45 oz'),
    'coffee_filters': ('Coffee filters', r'\bcoffee filters?\b', 'Melitta 8 12 Cup Basket Filters 100'),
    'cooking_oil': ('Cooking oil', r'\b(?:cooking|olive) oil\b', 'extra virgin olive oil mini bottle 60 ml'),
    'salt_pepper': ('Salt and pepper', r'\b(?:salt|pepper)\b', 'mini salt pepper shakers small refill portions'),
    'pillowcases': ('Pillowcases', r'\bpillow\s?cases?\b', 'California Design Den 400 Thread Count White Pillowcases'),
    'sheets': ('Sheets', r'\b(?:bed ?sheets?|fitted sheets?)\b', 'California Design Den 400 Thread Count White Sheet Set'),
    'plunger': ('Toilet plunger', r'\bplungers?\b', 'OXO Good Grips Toilet Plunger Canister'),
    'hair_dryer': ('Hair dryer', r'\b(?:hair|blow) ?dryers?\b', 'Conair 303 Mid Size 1875 Watt Hair Dryer'),
    'hangers': ('Clothes hangers', r'\bhangers?\b', 'Amazon Basics Slim Velvet Hangers 30 Pack'),
    'iron': ('Clothes iron', r'\b(?:clothes iron|steam iron)\b', 'BLACK DECKER Easy Steam IR40V'),
    'ironing_board': ('Ironing board', r'\bironing boards?\b', 'Honey Can Do Tabletop Ironing Board Iron Rest'),
    'phone_charger': ('Phone charger', r'\b(?:phone|usb|usb-c) chargers?\b', 'Anker 511 Nano 3 30W'),
    'coffee_maker': ('Coffee maker', r'\bcoffee ?makers?\b', 'Hamilton Beach 46299J Coffee Maker'),
    'toaster': ('Toaster', r'\btoasters?\b', 'Hamilton Beach 22614V Toaster'),
    'high_chair': ('High chair', r'\bhigh ?chairs?\b', 'Graco Slim Snacker Highchair'),
    'travel_crib': ('Travel crib or playard', r'\b(?:travel cribs?|playards?|pack.?n.?play)\b', 'Graco Pack n Play Portable Playard'),
    'chocolates': ('Optional apology chocolates', r'\bchocolates?\b', 'Ghirardelli Milk Chocolate Caramel 2 Squares single 1.06 oz bar'),
}

DEFAULTS = {
    'chocolates': ('Ghirardelli Milk Chocolate Caramel 2-Squares, single 1.06 oz bar', 'Optional gift. Full gift cost must stay within $3 and arrive with the essentials without delaying them. Skip if only bulk packs are available. Amazon listing, price, and Charlotte delivery remain unverified.', None, 'https://www.meijer.com/shopping/product/ghirardelli-milk-chocolate-caramel-2-squares-1-06-oz-bar/74759940722'),
    'trash_bags': ('Unscented 13-gallon trash bags, smallest practical pack', 'Supply 2 bags by default. Confirm bin size and buy the smallest suitable pack, not the previous 110-count box.', None, None),
    'laundry_pods': ('Fragrance-free laundry detergent pods, small trial or travel pack', 'Supply 2 pods by default. Confirm washer compatibility and choose the smallest practical pack rather than a 76-count tub.', None, None),
    'dish_pods': ('Dishwasher detergent pods, smallest practical pack', 'Supply 2 pods by default. Dishwasher detergent only; avoid a 62-count household restock.', None, None),
    'dish_soap': ('Dawn Free & Clear EZ-Squeeze, unscented, single 24.3 fl oz bottle', 'Selected unscented EZ-Squeeze single bottle; confirm the label and pack contents.', 'B0D38MJ3FH', 'https://www.amazon.com/dp/B0D38MJ3FH'),
    'toilet_paper': ('Charmin Ultra Strong, 6 Mega Rolls', 'Confirm larger rolls fit the holder.', 'B011DX5G92', 'https://www.ubuy.com.ph/product/N8BKAX6TO-charmin-ultra-strong-toilet-paper-6-mega-rolls-packaging-may-vary'),
    'paper_towels': ('Bounty Select-A-Size, white, 2 Triple Rolls', 'Choose enough for the stay without unnecessary bulk.', 'B0DQYR6X8R', 'https://app.parseshelf.com/best-sellers/asin/B0DQYR6X8R/'),
    'bath_soap': ('Dove Sensitive Beauty Bar, 8 bars, 3.75 oz each', 'Choose individually wrapped bars for guest use.', 'B001ET7IQK', 'https://www.bullsprice.com/product-details/B001ET7IQK/Beauty'),
    'batteries': ('Duracell Coppertop AA alkaline batteries, 8-pack', 'AA only. Confirm the device size; for AAA use Duracell 8-pack ASIN B003SIOZYI instead.', 'B003SI0TD4', 'https://pricehistory.app/p/duracell-coppertop-aa-batteries-power-boost-ingredients-cXITKvce'),
    'bath_towels': ('White cotton bath towels, 2-count pack, approximately 27 x 54 inches', 'Supply 2 bath towels unless the guest asks for a different quantity. Choose a suitable two-pack and confirm pack contents.', None, None),
    'hand_towels': ('White cotton hand towels, 2-count pack, approximately 16 x 28 inches', 'Supply 2 towels unless the guest asks for a different quantity. Choose the smallest suitable pack; do not default to a larger household restock.', None, None),
    'shampoo': ('Shampoo, single travel-size bottle, about 50–100 mL', 'Supply one small sealed bottle. Prefer a single bottle or small travel pack, not the previous 28 fl oz listing.', None, None),
    'conditioner': ('Conditioner, single travel-size bottle, about 50–100 mL', 'Supply one small sealed bottle. Prefer a single bottle or small travel pack, not the previous 28 fl oz listing.', None, None),
    'body_wash': ('Dove Ultra Sensitive Serum Body Wash, 18.5 fl oz', 'Choose Ultra Sensitive for the fragrance-free formula; check the exact label.', 'B0CNBTJ6NY', 'https://www.amazon.com/dp/B0CNBTJ6NY'),
    'sponges': ('Scotch-Brite Zero Scratch Scrub Sponges, 6-pack', 'Choose a small multipack and follow surface-care instructions.', 'B0043P0IAK', 'https://testmarket.io/proview/cleaning-supplies/scotch-brite-zero-scratch-scrub-sponges-for-cleaning-kitchen-bathroom-and-household-non-scratch-sponges-safe-for-non-stick-cookware-6-scrubbing-sponges-review-february-2024-the-ultimate-guide-with-in-depth-advantages-and-disadvantages-analysis-testmarket-expert-insights/2779'),
    'rinse_aid': ('Finish Jet-Dry rinse aid, single 8.45 fl oz bottle', 'Confirm the dishwasher has a rinse-aid compartment.', 'B0014E82II', 'https://www.desertcart.co.il/products/17077451-finish-jet-dry-dishwasher-rinse-aid-compatible-with-dishwasher-detergent'),
    'coffee_filters': ('Melitta natural brown basket filters, 8-12 cup, 100-count', 'Basket brewer only; confirm the coffee maker before selecting.', 'B00YY0OSFK', 'https://www.desertcart.com.sa/products/108917108-coffee-filter-basket-8-to-12-cup-100-count'),
    'cooking_oil': ('Extra virgin olive oil, single small bottle, about 60–100 mL', 'Provide a little oil in a sealed food-grade bottle. Prefer a mini bottle rather than the previous 16 fl oz bottle.', None, None),
    'salt_pepper': ('Salt and pepper, small refill portions or mini shakers', 'Provide a small amount of each, around 1 oz per mini shaker or small refill portions. Refill existing containers when possible; do not buy a full grinder set.', None, None),
    'blankets': ('Bedsure fleece blanket, grey, Queen, 90 x 90 inches', 'Queen 90 x 90 inches, grey. Confirm coverage and property color before using on a different bed size.', 'B0157T2ENY', 'https://img.team/products/B0157T2ENY'),
    'pillows': ('Beckham Hotel Collection down-alternative pillows, Standard/Queen, 2-pack', 'Standard/Queen only. Do not substitute for King pillows without checking the unit.', 'B01LYNW421', 'https://pricehistory.app/p/beckham-hotel-collection-bed-pillows-standard-queen-V7oAOIyp'),
    'pillowcases': ('California Design Den 400-thread-count cotton pillowcases, bright white, Standard/Queen, 2-pack', 'Standard/Queen only. Confirm the existing pillow size.', 'B0758C9NPB', 'https://www.asinsight.com/report/US/california-design-den'),
    'sheets': ('California Design Den 400-thread-count cotton sheet set, white, Queen', 'Queen only. Confirm bed size and mattress depth before using this listing.', 'B06XS1P6NG', 'https://app.parseshelf.com/best-sellers/asin/B06XS1P6NG/'),
    'plunger': ('OXO Good Grips toilet plunger with holder and drip tray, white', 'Includes enclosed storage and a drip tray.', 'B07GTHXKF3', 'https://pricehistory.app/p/oxo-good-grips-toilet-plunger-cEIS7ow8'),
    'hair_dryer': ('Conair 303XN Mid-Size 1875-Watt hair dryer, black', 'Choose a US model and plug.', 'B00GUAEERO', 'https://www.desertcart.com.au/products/32970670-conair-hair-dryer-1875w-mid-size-hair-dryer'),
    'hangers': ('Amazon Basics slim velvet hangers, black/silver, 30-pack', 'Match existing hangers and avoid unnecessary quantities.', 'B00FXNABPI', 'https://www.merchantwords.com/page1products/us/velvet%20hangers'),
    'iron': ('BLACK+DECKER Easy Steam IR40V compact iron with automatic shutoff', 'Select the version with automatic shutoff.', 'B01NB05WI5', 'https://www.algopix.com/products/B01NB05WI5-blackdecker-easy-steam-compact-iron-ir40v-nonstick-plate-smartsteam-antidrip-auto'),
    'ironing_board': ('Honey-Can-Do BRD-01435 tabletop ironing board with iron rest, aqua stripe, 32 x 12 inches', 'Confirm a suitable tabletop and storage space.', 'B0049U49SG', 'https://device.report/honey-can-do/brd-01435'),
    'phone_charger': ('Anker 511 Nano 3 A2147, 30W USB-C charger, US plug, Phantom Black', 'Choose a US plug and add the correct USB-C or Lightning cable; cable is separate.', 'B0B2MLRF93', 'https://service.anker.com/ca/article-description/How-to-Choose-a-Suitable-Wall-Charger-for-Your-Anker-Qi-Apple-MagSafe-Wireless-Charger'),
    'coffee_maker': ('Hamilton Beach 46299J 12-cup programmable coffee maker, black/stainless', 'Replaces discontinued 46292 recommendation. Confirm ground-coffee use and current availability.', 'B06XP4DKGL', 'https://www.amazon.com/dp/B06XP4DKGL'),
    'toaster': ('Hamilton Beach 22614V 2-slice metal toaster, black', 'Confirm two slots meet the property needs.', 'B00NMWRK2E', 'https://discompare.com/product/hamilton-beach-2-slice-toaster-with-extra-wide-slots-2/'),
    'high_chair': ('Graco Slim Snacker Highchair, Whisk', 'Manager review required: exact model, current recall status, suitability, and assembly.', 'B00PTL13LA', 'https://www.amazon.com/dp/B00PTL13LA'),
    'travel_crib': ('Graco Pack n Play Portable Playard, Carnival, model 1946962', 'Manager review required: exact model, current recall status, suitability, and manufacturer instructions.', 'B00Y286FVE', 'https://device.report/graco/1946962'),
}


def save(db, data, timestamp):
    scope = data.get('scope', 'unit')
    if scope not in ('shared', 'unit'):
        raise ValueError('Choose shared supplies or a unit TV remote')
    kind = data.get('item_type')
    if not isinstance(kind, str) or kind not in ITEMS or (scope == 'shared' and kind == 'tv_remote'):
        raise ValueError('Choose a supported item type; TV remotes must belong to a unit')
    property_id = 'shared' if scope == 'shared' else str(uuid.UUID(data.get('property_id', '')))
    if scope == 'unit' and not db.execute('SELECT id FROM rental_units WHERE id = ?', (property_id,)).fetchone():
        raise ValueError('Choose a unit imported from Hospitable')
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
        previous = db.execute('SELECT * FROM shared_products WHERE id = ? AND active = 1', (identifier,)).fetchone() if scope == 'shared' else db.execute('SELECT * FROM preferred_products WHERE id = ? AND active = 1', (identifier,)).fetchone()
        if not previous or (scope == 'unit' and previous['property_id'] != property_id) or previous['version'] != version:
            raise ValueError('Product changed; reload before editing')
    elif version != 0:
        raise ValueError('New products must have version zero')
    duplicate = db.execute('SELECT id FROM shared_products WHERE item_type = ? AND active = 1', (kind,)).fetchone() if scope == 'shared' else db.execute('SELECT id FROM preferred_products WHERE property_id = ? AND item_type = ? AND lower(room) = lower(?) AND active = 1', (property_id, kind, fields['room'])).fetchone()
    if duplicate and duplicate['id'] != identifier:
        raise ValueError('A preferred product already exists for this item. Edit it instead.')
    identifier = identifier or str(uuid.uuid4())
    if scope == 'shared':
        db.execute('INSERT INTO shared_products VALUES (?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(id) DO UPDATE SET item_type = excluded.item_type, title = excluded.title, asin = excluded.asin, version = excluded.version, updated_at = excluded.updated_at', (identifier, kind, fields['title'], asin, version + 1, timestamp, timestamp))
    else:
        db.execute('INSERT INTO preferred_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?) ON CONFLICT(id) DO UPDATE SET item_type = excluded.item_type, room = excluded.room, tv_brand = excluded.tv_brand, tv_model = excluded.tv_model, title = excluded.title, asin = excluded.asin, version = excluded.version, updated_at = excluded.updated_at', (identifier, property_id, kind, fields['room'], fields['tv_brand'], fields['tv_model'], fields['title'], asin, version + 1, timestamp, timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (property_id, 'preferred_product_saved', timestamp, json.dumps({'id': identifier, 'asin': asin, 'version': version + 1})))
    return {'id': identifier}
