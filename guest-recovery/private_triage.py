"""Prepare case actions from a verified shared stay and recent guest messages."""

import datetime as dt
import re
from html import escape

from private_supply import approval_form, observe
from purchase_plans import build
from reply_library import decision_request
from research import analyze
from safety import detect, guest_message


def case_actions(db, fields, data, now=None):
    now = now or dt.datetime.now(dt.UTC)
    property_id = fields['property_external_id']
    booking_id = fields['reservation_external_id']
    try:
        check_in = dt.datetime.fromisoformat(data['effective_check_in'])
        check_out = dt.datetime.fromisoformat(data['effective_check_out'])
    except (KeyError, ValueError, TypeError):
        return {}
    if not check_in.tzinfo or not check_out.tzinfo or not now < check_out:
        return {}
    messages = []
    for item in data['messages']:
        if item.get('truncated') or not all(isinstance(item.get(k), str) for k in ('id', 'sent_at', 'body')):
            continue
        try:
            instant = dt.datetime.fromisoformat(item['sent_at'])
        except ValueError:
            continue
        if instant.tzinfo is None:
            continue
        messages.append({
            'id': item['id'], 'created_at': instant.astimezone(dt.UTC).isoformat(),
            'body': item['body'], 'sender_type': 'guest',
        })
    if not messages:
        return {}
    snapshot = {
        'property_count': 1, 'synced_at': now.isoformat(), 'nontext_message_count': 0, 'scope': 'shared',
        'active_reservation_ids': [booking_id] if check_in <= now else [],
        'upcoming_reservation_ids': [booking_id] if check_in > now else [],
        'conversations': [{
            'property': data.get('property_name') or 'This property',
            'property_ids': [property_id], 'reservation_id': booking_id,
            'check_in': check_in.isoformat(), 'check_out': check_out.isoformat(),
            'conversation_id': booking_id, 'messages': messages,
        }],
    }
    issues = analyze(snapshot, current=now)['issues']
    actions = {}
    supply_action = None
    supply_message_id = None
    for issue in issues:
        latest = issue['evidence'][-1]
        action = actions.setdefault(latest['id'], {})
        category = issue['category']
        if category == 'Safety':
            kind = detect(latest['body'])
            if kind:
                action['safety'] = (
                    guest_message(kind, city='').replace('call 911', 'call local emergency services')
                )
            continue
        if issue['resolution_hint'] or issue['lane'] != 'needs_attention':
            continue
        if category == 'Supplies' and not detect(latest['body']):
            combined = {**latest, 'body': '\n'.join(dict.fromkeys(item['body'] for item in issue['evidence']))}
            plan = build(db, issue, combined, {'gift': False, 'pack_sizes': {}})
            if plan['lines']:
                action['supplies'] = plan['lines']
                supply_action = action
                supply_message_id = latest['id']
        elif category in ('Maintenance', 'Cleaning'):
            action[category.lower()] = True
        elif category == 'Instructions':
            action['stay_request'] = decision_request(latest['body'])
            if not action['stay_request']:
                action['instructions'] = True
    proposal = observe(
        db, fields, snapshot['conversations'][0]['property'], check_out,
        supply_message_id, supply_action['supplies'] if supply_action else None, now,
    )
    if supply_action is not None:
        supply_action['proposal'] = proposal
    return actions


def render_actions(action, *, cleaning_access=False, repair_access=False, csrf=None, fields=None):
    if action.get('safety'):
        return (
            '<aside class="case-action case-action-urgent"><strong>Safety report · Review now</strong><p>'
            + escape(action['safety']) + '</p></aside>'
        )
    body = []
    if repair_access and action.get('maintenance'):
        body.append('<p><a href="#repair-request">Open Maintenance intake</a></p>')
    if cleaning_access and action.get('cleaning'):
        body.append('<p><a href="#cleaning-touchup">Request a cleaning touch-up</a></p>')
    if cleaning_access and action.get('stay_request'):
        body.append('<p><a href="#stay-change">Check this stay request in Cleaner</a></p>')
    if action.get('instructions') and not (action.get('supplies') or action.get('maintenance')):
        body.append('<p>Property question. Check the property’s current instructions before drafting a reply.</p>')
    if action.get('supplies'):
        lines = []
        for item in action['supplies']:
            title = escape(item['title'])
            asin = item.get('asin')
            label = (
                f'<a href="https://www.amazon.com/dp/{asin}" rel="noreferrer">{title}</a>'
                if isinstance(asin, str) and re.fullmatch(r'[A-Z0-9]{10}', asin)
                else title + ' · Choose a compatible product'
            )
            packs = item['packs']
            quantity = f'{packs} pack' + ('s' if packs != 1 else '') if packs else 'Confirm pack size'
            lines.append('<li>' + label + ' · ' + escape(quantity) + '<p>' + escape(item['check']) + '</p></li>')
        body.append(
            '<strong>Suggested supplies</strong><ul>' + ''.join(lines) + '</ul>'
            + '<p>Confirm stock at the property, the total, and delivery before you place an order.</p>'
            + (approval_form(csrf, fields, action.get('proposal')) if csrf and fields else '')
        )
    return '<aside class="case-action">' + ''.join(body) + '</aside>' if body else ''
