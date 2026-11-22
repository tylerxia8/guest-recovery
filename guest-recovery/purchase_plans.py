import hashlib
import json
import math
import re
from urllib.parse import urlencode

BUNDLES = {'B0DFMNSPXW': ('shampoo', 'conditioner')}

PACK_SIZES = {'B0DFMNSPXW': 1, 'B01J1ZLOUO': 2, 'B07B6GC2R5': 2, 'B00QEMWJ70': 16, 'B0DTB79QJY': 12, 'B01DM5EZ50': 20, 'B000R95C8O': 1, 'B0D3S6RXQR': 1, 'B000VM2O5Q': 6, 'B08CY8YPZR': 4, 'B08JQ672VN': 4, 'B0CK9RZGDX': 110, 'B0BSJSFY1M': 76, 'B08VPYMJ43': 62, 'B011DX5G92': 6, 'B0DQYR6X8R': 2, 'B001ET7IQK': 8, 'B003SI0TD4': 8, 'B003SIOZYI': 8, 'B0043P0IAK': 6, 'B00YY0OSFK': 100, 'B01LYNW421': 2, 'B0758C9NPB': 2, 'B00FXNABPI': 30}


def build(db, issue, message, choices=None):
    from catalog import DEFAULTS, ITEMS
    from fulfillment import profile, quantities
    from guest_replies import draft
    saved = db.execute('SELECT * FROM purchase_plans WHERE issue_id = ? AND message_id = ?', (issue['id'], message['id'])).fetchone()
    previous = json.loads(saved['body']) if saved else None
    choices = choices if choices is not None else previous['choices'] if previous else {'gift': True, 'pack_sizes': {}}
    if type(choices.get('gift')) is not bool or not isinstance(choices.get('pack_sizes'), dict):
        raise ValueError('Choose optional chocolates and valid pack sizes')
    destination = profile(db, issue['property_ids'][0]) if len(issue['property_ids']) == 1 else None
    kinds = [kind for kind in issue['item_types'] if kind != 'chocolates' and re.search(ITEMS[kind][1], message['body'].replace('\u2019', "'"), re.IGNORECASE)]
    amounts = quantities({**issue, 'item_types': kinds, 'evidence': [message]}, destination)
    products = [dict(row) for row in db.execute('SELECT * FROM preferred_products WHERE active = 1 AND property_id = ? ORDER BY item_type, room, id', (destination['property_id'],))] if destination else []
    products = [product for product in products if product['item_type'] == 'tv_remote']
    products += [{**dict(row), 'room': '', 'tv_brand': '', 'tv_model': ''} for row in db.execute('SELECT * FROM shared_products WHERE active = 1 ORDER BY item_type')]
    lines = []
    blockers = []
    for amount in amounts:
        kind = amount['item_type']
        matches = [product for product in products if product['item_type'] == kind]
        product = matches[0] if len(matches) == 1 else None
        shared = DEFAULTS.get(kind) if not matches and kind != 'tv_remote' else None
        asin = product['asin'] if product else shared[2] if shared else None
        title = product['title'] if product else shared[0] if shared else ITEMS[kind][0]
        units = PACK_SIZES.get(asin)
        override = choices['pack_sizes'].get(kind)
        if override is not None:
            if not isinstance(override, dict) or type(override.get('units')) is not int or not 1 <= override['units'] <= 1000 or not isinstance(override.get('asin'), str):
                raise ValueError('Pack sizes must be whole numbers between 1 and 1000')
            if override['asin'] == asin:
                if kind in BUNDLES.get(asin, ()) and override['units'] != 1:
                    blockers.append('Saved travel-set pack size needs review. Save the current plan to use one shampoo and one conditioner per set.')
                else:
                    units = override['units']
        lines.append({**amount, 'label': ITEMS[kind][0], 'title': title, 'asin': asin, 'units_per_pack': units, 'packs': math.ceil(amount['quantity'] / units) if units else None, 'check': (product['room'] + ' · ' + product['tv_brand'] + ' ' + product['tv_model']) if product and kind == 'tv_remote' else shared[1] if shared else 'Verify the selected unit product and pack contents.', 'bundle': kind in BUNDLES.get(asin, ()), 'url': 'https://www.amazon.com/dp/' + asin if asin else 'https://www.amazon.com/s?' + urlencode({'k': title})})
    if issue['lane'] == 'practice':
        blockers.append('Practice case; do not order for this historical or unverified stay.')
    if issue['resolution_hint']:
        blockers.append('The guest reported a possible resolution. Review it before ordering.')
    if not destination or not destination['verified']:
        blockers.append('Complete the property delivery details.')
    if not lines:
        blockers.append('The selected text does not identify a supply item. Review the conversation.')
    if any(not line['asin'] for line in lines):
        blockers.append('Choose an exact compatible preferred product for each item in Settings.')
    if any(not line['units_per_pack'] for line in lines):
        blockers.append('Enter the selected product’s units per pack.')
    fingerprint = hashlib.sha256(json.dumps({'issue': issue['id'], 'message': message['id'], 'messages': issue['messages'], 'source': issue['source_messages_fingerprint'], 'lane': issue['lane'], 'resolution': issue['resolution_hint'], 'destination': destination, 'products': products, 'defaults': DEFAULTS, 'pack_sizes': PACK_SIZES, 'bundles': BUNDLES, 'amounts': amounts}, sort_keys=True).encode()).hexdigest()
    order_lines = []
    for line in lines:
        existing = next((item for item in order_lines if item['asin'] and item['asin'] == line['asin']), None)
        if existing and line['bundle']:
            existing['packs'] = max(existing['packs'], line['packs'])
            existing['needs'].append(str(line['quantity']) + ' ' + line['label'].lower())
        else:
            order_lines.append({'title': line['title'], 'asin': line['asin'], 'packs': line['packs'], 'needs': [str(line['quantity']) + ' ' + line['label'].lower()]})
    summary = '; '.join(item['title'] + ' · ' + (str(item['packs']) + ' pack(s)' if item['packs'] else 'pack size needed') + ' · covers ' + ', '.join(item['needs']) for item in order_lines)

    gift_product = next((product for product in products if product['item_type'] == 'chocolates'), None)
    gift_title = gift_product['title'] if gift_product else DEFAULTS['chocolates'][0]
    gift_url = 'https://www.amazon.com/dp/' + gift_product['asin'] if gift_product else 'https://www.amazon.com/s?' + urlencode({'k': gift_title})
    gift = {'title': gift_title, 'maximum_cents': 300, 'requested': choices['gift'], 'url': gift_url, 'check': 'Add only if the full gift cost is $3 or less, fits the order budget, and arrives with the essentials without delaying them. Otherwise skip.'}
    notice = 'PURCHASE PROPOSAL — NOT ORDERED\nProperty: ' + issue['property'] + '\n' + summary + ('\nOptional gift: ' + gift_title + ', up to $3, only without delaying essentials.' if choices['gift'] else '') + '\nThe manager places the Amazon order after checking current price and delivery.'
    return {'message_id': message['id'], 'lines': lines, 'order_lines': order_lines, 'destination': destination, 'gift': gift, 'choices': choices, 'blockers': blockers, 'summary': summary, 'manager_notice': notice, 'guest_acknowledgment': draft(db, issue, message)['suggested'], 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'stale': bool(saved and saved['source_fingerprint'] != fingerprint), 'saved_at': saved['updated_at'] if saved else None}


def save(db, data, timestamp):
    from research import analyze
    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not snapshot:
        raise ValueError('Refresh Hospitable before preparing a purchase')
    issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == data.get('issue_id') and item['category'] == 'Supplies'), None)
    message = next((item for item in issue['evidence'] if item['id'] == data.get('message_id') and item['sender_type'] == 'guest'), None) if issue else None
    if not message:
        raise ValueError('Choose a guest supply request')
    current = build(db, issue, message)
    if type(data.get('version')) is not int or data['version'] != current['version'] or data.get('source_fingerprint') != current['source_fingerprint']:
        raise ValueError('This plan or conversation changed. Reload and review before saving.')
    choices = data.get('choices')
    if not isinstance(choices, dict) or type(choices.get('gift')) is not bool or not isinstance(choices.get('pack_sizes'), dict):
        raise ValueError('Choose valid plan options and pack sizes')
    selected = {line['item_type']: line['asin'] for line in current['lines']}
    for kind, size in choices['pack_sizes'].items():
        if kind not in selected or not selected[kind] or not isinstance(size, dict) or size.get('asin') != selected[kind]:
            raise ValueError('The preferred product changed; review the selected pack again')
        if kind in BUNDLES.get(size['asin'], ()) and size.get('units') != 1:
            raise ValueError('The travel set contains one shampoo and one conditioner per pack')
    plan = build(db, issue, message, choices)
    db.execute('INSERT INTO purchase_plans VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(issue_id, message_id) DO UPDATE SET body = excluded.body, version = excluded.version, source_fingerprint = excluded.source_fingerprint, updated_at = excluded.updated_at', (issue['id'], message['id'], json.dumps(plan), current['version'] + 1, plan['source_fingerprint'], timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'purchase_plan_saved', timestamp, json.dumps({'message_id': message['id'], 'version': current['version'] + 1, 'gift_requested': choices['gift'], 'ordered': False, 'notified': False})))
    return {'saved': True, 'ordered': False, 'notified': False}
