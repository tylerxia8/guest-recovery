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
    'coffee_maker': ('Coffee maker', r'\bcoffee ?makers?\b', 'Hamilton Beach 46299J Coffee Maker'),
    'toaster': ('Toaster', r'\btoasters?\b', 'Hamilton Beach 22614V Toaster'),
    'high_chair': ('High chair', r'\bhigh ?chairs?\b', 'Graco Slim Snacker Highchair'),
    'travel_crib': ('Travel crib or playard', r'\b(?:travel cribs?|playards?|pack.?n.?play)\b', 'Graco Pack n Play Portable Playard'),
}

DEFAULTS = {
    'trash_bags': ('Glad ForceFlex fragrance-free 13-gallon drawstring trash bags, 110-count', 'Confirm bin size; custom-fit bins may require specific liners.', 'B0CK9RZGDX', 'https://www.amazon.com/dp/B0CK9RZGDX'),
    'laundry_pods': ('Tide PODS Free & Gentle, 76-count', 'Confirm the washer permits pods; choose a practical pack for the stay.', 'B0BSJSFY1M', 'https://kortingoutlet.com/product/lpnq1lvvm13fhk/'),
    'dish_pods': ('Cascade Free & Clear ActionPacs, Lemon Essence, 62-count', 'Dishwasher detergent only; keep separate from laundry pods.', 'B08VPYMJ43', 'https://kortingoutlet.com/product/lpnspqy9ndrlvz/'),
    'dish_soap': ('Dawn Free & Clear EZ-Squeeze, unscented, single 24.3 fl oz bottle', 'Selected unscented EZ-Squeeze single bottle; confirm the label and pack contents.', 'B0D38MJ3FH', 'https://www.amazon.com/dp/B0D38MJ3FH'),
    'toilet_paper': ('Charmin Ultra Strong, 6 Mega Rolls', 'Confirm larger rolls fit the holder.', 'B011DX5G92', 'https://www.ubuy.com.ph/product/N8BKAX6TO-charmin-ultra-strong-toilet-paper-6-mega-rolls-packaging-may-vary'),
    'paper_towels': ('Bounty Select-A-Size, white, 2 Triple Rolls', 'Choose enough for the stay without unnecessary bulk.', 'B0DQYR6X8R', 'https://app.parseshelf.com/best-sellers/asin/B0DQYR6X8R/'),
    'bath_soap': ('Dove Sensitive Beauty Bar, 8 bars, 3.75 oz each', 'Choose individually wrapped bars for guest use.', 'B001ET7IQK', 'https://www.bullsprice.com/product-details/B001ET7IQK/Beauty'),
    'batteries': ('Duracell Coppertop AA alkaline batteries, 8-pack', 'AA only. Confirm the device size; for AAA use Duracell 8-pack ASIN B003SIOZYI instead.', 'B003SI0TD4', 'https://pricehistory.app/p/duracell-coppertop-aa-batteries-power-boost-ingredients-cXITKvce'),
    'bath_towels': ('American Soft Linen bright white bath towels, 4-pack, 27 x 54 inches', 'Match existing towels and confirm pack contents.', 'B08JQ672VN', 'https://www.amazon.com/dp/B08JQ672VN'),
    'hand_towels': ('American Soft Linen bright white hand towels, 4-pack, 16 x 28 inches', 'Match existing color and confirm pack contents.', 'B08CY8YPZR', 'https://www.bathroomdesignhub.com/products/american-soft-linen-100-cotton-luxury-turkish-towels-b08cy8ypzr/'),
    'shampoo': ('Dove Daily Moisture Shampoo, 28 fl oz', 'Match the existing dispenser or use a sealed bottle.', 'B0DK5VYD9F', 'https://www.amazon.com/dp/B0DK5VYD9F'),
    'conditioner': ('Dove Daily Moisture Conditioner, 28 fl oz', 'Match the shampoo and existing dispenser.', 'B0DK5VDFGZ', 'https://www.amazon.com/dp/B0DK5VDFGZ'),
    'body_wash': ('Dove Ultra Sensitive Serum Body Wash, 18.5 fl oz', 'Choose Ultra Sensitive for the fragrance-free formula; check the exact label.', 'B0CNBTJ6NY', 'https://www.amazon.com/dp/B0CNBTJ6NY'),
    'sponges': ('Scotch-Brite Zero Scratch Scrub Sponges, 6-pack', 'Choose a small multipack and follow surface-care instructions.', 'B0043P0IAK', 'https://testmarket.io/proview/cleaning-supplies/scotch-brite-zero-scratch-scrub-sponges-for-cleaning-kitchen-bathroom-and-household-non-scratch-sponges-safe-for-non-stick-cookware-6-scrubbing-sponges-review-february-2024-the-ultimate-guide-with-in-depth-advantages-and-disadvantages-analysis-testmarket-expert-insights/2779'),
    'rinse_aid': ('Finish Jet-Dry rinse aid, single 8.45 fl oz bottle', 'Confirm the dishwasher has a rinse-aid compartment.', 'B0014E82II', 'https://www.desertcart.co.il/products/17077451-finish-jet-dry-dishwasher-rinse-aid-compatible-with-dishwasher-detergent'),
    'coffee_filters': ('Melitta natural brown basket filters, 8-12 cup, 100-count', 'Basket brewer only; confirm the coffee maker before selecting.', 'B00YY0OSFK', 'https://www.desertcart.com.sa/products/108917108-coffee-filter-basket-8-to-12-cup-100-count'),
    'cooking_oil': ('Pompeian Smooth Extra Virgin Olive Oil, single 16 fl oz bottle', 'Choose a sealed bottle and avoid unnecessary bulk.', 'B015ONU4JE', 'https://bazar.earth/products/B015ONU4JE'),
    'salt_pepper': ('OXO Good Grips salt and pepper grinder set, stainless steel, prefilled', 'Replace missing grinders; replenish existing ones instead when possible.', 'B001CDP5UO', 'https://www.tiendamia.cr/p/amz/b001cdp5uo/oxo-good-grips-juego-de-molinillo-de-sal-y'),
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
