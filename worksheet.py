import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CHOICES = {
    'guest_need': ('unknown', 'open', 'resolved'),
    'provided': ('unknown', 'yes', 'no'),
    'stock': ('unknown', 'accessible', 'unavailable'),
    'housekeeping': ('unknown', 'available', 'unavailable'),
    'address_verified': ('unknown', 'yes', 'no'),
    'delivery_verified': ('unknown', 'yes', 'no'),
}
TEXT = {'stock_location': 500, 'notes': 2000, 'housekeeping_arrival': 40, 'product_title': 300, 'asin': 10, 'delivery_date': 10, 'property_timezone': 100}
NUMBERS = {'quantity': (1, 20, 2), 'total_cents': (0, 3500, 0), 'gift_cents': (0, 300, 0), 'minimum_stock': (1, 100, 4)}


def validate(data):
    if not isinstance(data, dict) or set(data) != set(CHOICES) | set(TEXT) | set(NUMBERS):
        raise ValueError('Worksheet fields do not match the expected format')
    for key, values in CHOICES.items():
        if data[key] not in values:
            raise ValueError('Invalid ' + key)
    for key, maximum in TEXT.items():
        if not isinstance(data[key], str) or len(data[key]) > maximum:
            raise ValueError('Invalid ' + key)
        data[key] = data[key].strip()
    for key, (minimum, maximum, default) in NUMBERS.items():
        if type(data[key]) is not int or not minimum <= data[key] <= maximum:
            raise ValueError('Invalid ' + key)
    if data['gift_cents'] > data['total_cents']:
        raise ValueError('Gift must be included in the total')
    if data['asin'] and not re.fullmatch(r'[A-Z0-9]{10}', data['asin']):
        raise ValueError('Amazon ASIN must have 10 uppercase letters or digits')
    if data['delivery_date']:
        dt.date.fromisoformat(data['delivery_date'])
    if data['housekeeping_arrival']:
        arrival = dt.datetime.fromisoformat(data['housekeeping_arrival'])
        if arrival.tzinfo is None:
            raise ValueError('Housekeeping arrival requires a timezone offset')
    if data['property_timezone']:
        try:
            ZoneInfo(data['property_timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Enter a valid property timezone, such as America/New_York') from None
    return data


def build(issue, saved):
    fingerprint = hashlib.sha256(json.dumps({'messages': issue['messages'], 'check_in': issue['check_in'], 'check_out': issue['check_out']}, sort_keys=True).encode()).hexdigest()
    inputs = {key: 'unknown' for key in CHOICES} | {key: '' for key in TEXT} | {key: values[2] for key, values in NUMBERS.items()}
    if saved:
        inputs = json.loads(saved['body'])
    stale = bool(saved and saved['source_fingerprint'] != fingerprint)
    current = dt.datetime.now(dt.timezone.utc)
    check_in = dt.datetime.fromisoformat(issue['check_in'].replace('Z', '+00:00'))
    checkout = dt.datetime.fromisoformat(issue['check_out'].replace('Z', '+00:00'))
    active = check_in <= current < checkout
    checks = [{'label': 'Reservation is currently in-house', 'status': 'confirmed' if active else 'needs_decision'}, {'label': 'Conversation reviewed; guest still needs hand towels', 'status': 'confirmed' if inputs['guest_need'] == 'open' and not stale else 'unknown' if inputs['guest_need'] == 'unknown' or stale else 'needs_decision'}, {'label': 'Hand towels are included at this property', 'status': 'confirmed' if inputs['provided'] == 'yes' else 'unknown' if inputs['provided'] == 'unknown' else 'needs_decision'}]
    blockers = []
    if stale:
        blockers.append('The conversation changed after this worksheet was saved. Review it and save again to confirm your findings.')
    if not active:
        blockers.append('The reservation is not currently in-house. Keep this as historical research and review any proposed action.')
    if inputs['guest_need'] == 'unknown':
        blockers.append('Review later guest and host messages, previous orders, and manager actions to confirm the request remains open.')
    if inputs['provided'] == 'unknown':
        blockers.append('Confirm whether hand towels are included at this property.')
    if inputs['provided'] == 'no':
        blockers.append('Manager decision needed: hand towels are not part of the recorded property offering.')
    route = 'Investigate'
    proposal = 'Confirm the guest need and property supplies before choosing a remedy.'
    if inputs['guest_need'] == 'resolved':
        route = 'No fulfillment needed'
        proposal = 'The reviewer recorded this request as already resolved. Keep the evidence and improve stocking where appropriate.'
        blockers = [item for item in blockers if stale and item.startswith('The conversation changed')]
    elif inputs['guest_need'] == 'open' and inputs['provided'] == 'yes':
        if inputs['stock'] == 'unknown':
            blockers.append('Ask the manager or housekeeping whether spare hand towels are accessible onsite.')
        elif inputs['stock'] == 'accessible':
            route = 'Use onsite stock'
            proposal = 'Prepare property-specific directions to the spare hand towels for manager review.'
            if not inputs['stock_location']:
                blockers.append('Record the verified location and access instructions for spare hand towels.')
        elif inputs['housekeeping'] == 'unknown':
            blockers.append('Check whether housekeeping can deliver hand towels before considering a purchase.')
        else:
            arrival = dt.datetime.fromisoformat(inputs['housekeeping_arrival']) if inputs['housekeeping_arrival'] else None
            if inputs['housekeeping'] == 'available' and arrival is None:
                blockers.append('Record the confirmed housekeeping arrival time, including its timezone offset.')
            elif inputs['housekeeping'] == 'available' and current < arrival < checkout:
                route = 'Housekeeping delivery'
                proposal = 'Propose a housekeeping delivery at ' + inputs['housekeeping_arrival'] + '. No dispatch has been made.'
            else:
                route = 'Replacement purchase proposal'
                proposal = 'Prepare a hand-towel purchase for manager review. Nothing will be ordered.'
                if inputs['housekeeping'] == 'available':
                    blockers.append('The recorded housekeeping arrival is no longer viable; reconfirm availability or mark it unavailable.')
                for key, label in [('product_title', 'Select a manager-approved replacement product.'), ('asin', 'Record the replacement Amazon ASIN.'), ('property_timezone', 'Record the property timezone.'), ('delivery_date', 'Record the verified delivery date for every item.')]:
                    if not inputs[key]:
                        blockers.append(label)
                if inputs['total_cents'] <= 0:
                    blockers.append('Record the all-in price, including quantity, gift, taxes, and delivery. The proposal limit is $35.')
                for key, label in [('address_verified', 'Confirm the delivery address matches the reservation property.'), ('delivery_verified', 'Confirm every item, including an optional gift, can arrive on the recorded date.')]:
                    if inputs[key] != 'yes':
                        blockers.append(label)
                if inputs['delivery_date'] and inputs['property_timezone']:
                    zone = ZoneInfo(inputs['property_timezone'])
                    delivery = dt.date.fromisoformat(inputs['delivery_date'])
                    if not current.astimezone(zone).date() <= delivery < checkout.astimezone(zone).date():
                        blockers.append('Delivery must be today or later and before the guest checkout date. Escalate if no suitable delivery is available.')
    checks.append({'label': 'Remedy requirements are complete', 'status': 'confirmed' if not blockers else 'needs_decision'})
    return {'inputs': inputs, 'version': saved['version'] if saved else 0, 'source_fingerprint': fingerprint, 'stale': stale, 'checks': checks, 'blockers': blockers, 'route': route, 'proposal': proposal, 'readiness': 'Needs review' if blockers else 'Proposal prepared for review', 'prevention': 'Keep at least ' + str(inputs['minimum_stock']) + ' hand towels onsite and add a hand-towel stock check to the turnover checklist. This is a proposed policy, not an inventory count.', 'saved_at': saved['updated_at'] if saved else None}
