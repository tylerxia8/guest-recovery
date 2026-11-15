import hashlib
import json
import math
import re
from urllib.parse import urlencode

PACK_SIZES = {'B08CY8YPZR': 4, 'B08JQ672VN': 4, 'B0CK9RZGDX': 110, 'B0BSJSFY1M': 76, 'B08VPYMJ43': 62, 'B011DX5G92': 6, 'B0DQYR6X8R': 2, 'B001ET7IQK': 8, 'B003SI0TD4': 8, 'B003SIOZYI': 8, 'B0043P0IAK': 6, 'B00YY0OSFK': 100, 'B01LYNW421': 2, 'B0758C9NPB': 2, 'B00FXNABPI': 30}


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
    kinds = [kind for kind in issue['item_types'] if kind != 'chocolates' and re.search(ITEMS[kind][1], message['body'].replace('\u2019', "'"), re.I)]
    amounts = quantities({**issue, 'item_types': kinds, 'evidence': [message]}, destination)
    products = [dict(row) for row in db.execute('SELECT * FROM preferred_products WHERE active = 1 AND property_id = ? ORDER BY item_type, room, id', (destination['property_id'],))] if destination else []
    lines = []
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
                units = override['units']
        lines.append({**amount, 'label': ITEMS[kind][0], 'title': title, 'asin': asin, 'units_per_pack': units, 'packs': math.ceil(amount['quantity'] / units) if units else None, 'check': (product['room'] + ' · ' + product['tv_brand'] + ' ' + product['tv_model']) if product and kind == 'tv_remote' else shared[1] if shared else 'Verify the selected unit product and pack contents.', 'url': 'https://www.amazon.com/dp/' + asin if asin else 'https://www.amazon.com/s?' + urlencode({'k': title})})
    blockers = []
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
    fingerprint = hashlib.sha256(json.dumps({'issue': issue['id'], 'message': message['id'], 'messages': issue['messages'], 'source': issue['source_messages_fingerprint'], 'lane': issue['lane'], 'resolution': issue['resolution_hint'], 'destination': destination, 'products': products, 'defaults': DEFAULTS, 'pack_sizes': PACK_SIZES, 'amounts': amounts}, sort_keys=True).encode()).hexdigest()
    summary = '; '.join(line['title'] + ' · ' + (str(line['packs']) + ' pack(s)' if line['packs'] else 'pack size needed') + ' · need ' + str(line['quantity']) + ' ' + line['unit'] for line in lines)
    gift = {'title': DEFAULTS['chocolates'][0], 'maximum_cents': 300, 'requested': choices['gift'], 'url': 'https://www.amazon.com/s?' + urlencode({'k': DEFAULTS['chocolates'][0]}), 'check': 'Add only if the full gift cost is $3 or less, fits the order budget, and arrives with the essentials without delaying them. Otherwise skip.'}
    notice = 'PURCHASE PROPOSAL — NOT ORDERED\nProperty: ' + issue['property'] + '\n' + summary + ('\nOptional Ghirardelli chocolates, up to $3, only without delaying essentials.' if choices['gift'] else '') + '\nAmazon price and arrival remain unverified. Totals up to $25 require a successful manager notice and a 10-minute veto window; above $25 requires manager approval before purchase. Checkout is manual.'
    return {'message_id': message['id'], 'lines': lines, 'destination': destination, 'gift': gift, 'choices': choices, 'blockers': blockers, 'summary': summary, 'manager_notice': notice, 'guest_acknowledgment': draft(db, issue, message)['suggested'], 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'stale': bool(saved and saved['source_fingerprint'] != fingerprint), 'saved_at': saved['updated_at'] if saved else None}


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
    if not isinstance(choices, dict):
        raise ValueError('Choose plan options')
    if not isinstance(choices.get('pack_sizes'), dict):
        raise ValueError('Enter valid pack sizes')
    selected = {line['item_type']: line['asin'] for line in current['lines']}
    for kind, size in choices['pack_sizes'].items():
        if kind not in selected or not selected[kind] or not isinstance(size, dict) or size.get('asin') != selected[kind]:
            raise ValueError('The preferred product changed; review the selected pack again')
    plan = build(db, issue, message, choices)
    db.execute('INSERT INTO purchase_plans VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(issue_id, message_id) DO UPDATE SET body = excluded.body, version = excluded.version, source_fingerprint = excluded.source_fingerprint, updated_at = excluded.updated_at', (issue['id'], message['id'], json.dumps(plan), current['version'] + 1, plan['source_fingerprint'], timestamp))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'purchase_plan_saved', timestamp, json.dumps({'message_id': message['id'], 'version': current['version'] + 1, 'gift_requested': choices['gift'], 'ordered': False, 'notified': False})))
    return {'saved': True, 'ordered': False, 'notified': False}
