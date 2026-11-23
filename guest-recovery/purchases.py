import datetime as dt
import json
import logging
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mailer import email_ready, resend, send_email


def email_settings():
    from settings import email_configuration
    return email_configuration()


def describe(row):
    request = dict(row)
    current = dt.datetime.now(dt.UTC)
    request['eligible_at'] = None
    if row['decision'] == 'vetoed':
        request['status'] = 'Cancelled by manager'
    elif row['notice_status'] == 'failed':
        request['status'] = 'Email outcome unconfirmed — purchase blocked' if (row['email_id'] or '').startswith('smtp:') else 'Email failed — purchase blocked'
    elif not row['notified_at'] or (row['notice_status'] != 'delivered' and not (row['email_id'] or '').startswith('smtp:')):
        request['status'] = 'Waiting for email delivery'
    else:
        request['status'] = 'Manager to place Amazon order'
    request['email_evidence'] = 'Gmail accepted the message; inbox delivery is not verified.' if (row['email_id'] or '').startswith('smtp:') and row['notified_at'] else 'Email delivery confirmed by provider.' if row['notice_status'] == 'delivered' else 'Notification is not confirmed.'
    request['approval_required'] = True
    request['simulation'] = True
    request['offer'] = json.loads(row['offer']) if row['offer'] else None
    if request['offer']:
        request['offer']['stale'] = request['offer']['arrival_date'] < current.astimezone(ZoneInfo(request['offer']['timezone'])).date().isoformat()
    request.pop('email_payload')
    request.pop('email_id')
    return request


def change(db, path, data, timestamp):
    if path == '/api/purchases':
        from research import analyze
        issue_id = data.get('issue_id')
        summary = data.get('summary')
        total = data.get('total_cents')
        gift = data.get('gift_cents')
        if not isinstance(issue_id, str) or not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 1000:
            raise ValueError('Describe all proposed items')
        if type(total) is not int or not 0 < total <= 1000000 or type(gift) is not int or not 0 <= gift <= min(300, total):
            raise ValueError('Invalid total or gift cost; the gift must be included in the total and cost no more than $3')
        if data.get('all_in_confirmed') is not True:
            raise ValueError('Confirm that the total includes all items, tax, shipping, and fees')
        offer = data.get('offer')
        if offer is not None:
            if not isinstance(offer, dict):
                raise ValueError('Invalid offer details')
            location = offer.get('location', 'Charlotte, NC')
            window = offer.get('delivery_window')
            asins = offer.get('asins')
            timezone = offer.get('timezone', 'America/New_York')
            if not isinstance(timezone, str) or len(timezone) > 100:
                raise ValueError('Choose a valid property time zone')
            try:
                ZoneInfo(timezone)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError('Choose a valid property time zone') from None
            if not isinstance(location, str) or not 1 <= len(location.strip()) <= 200 or not isinstance(window, str) or not 1 <= len(window.strip()) <= 200:
                raise ValueError('Enter the delivery location and Amazon arrival window')
            if not isinstance(asins, list) or not 1 <= len(asins) <= 10 or any(not isinstance(asin, str) or not re.fullmatch(r'[A-Z0-9]{10}', asin) for asin in asins):
                raise ValueError('Enter up to 10 valid Amazon ASINs')
            try:
                arrival = dt.date.fromisoformat(offer.get('arrival_date', ''))
            except (ValueError, TypeError):
                raise ValueError('Enter a valid arrival date') from None
            if arrival < dt.datetime.now(ZoneInfo(timezone)).date():
                raise ValueError('Arrival date cannot be in the past')
            if offer.get('address_confirmed') is not True:
                raise ValueError('Confirm compatibility and the arrival estimate at the actual property address')
            offer = {'location': location.strip(), 'timezone': timezone, 'asins': list(dict.fromkeys(asins)), 'arrival_date': arrival.isoformat(), 'delivery_window': window.strip(), 'address_confirmed': True, 'recorded_at': timestamp, 'source': 'Manager-entered Amazon estimate; not automatically verified'}
        snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        if not snapshot:
            raise ValueError('Research snapshot is missing')
        issue = next((item for item in analyze(json.loads(snapshot['body']))['issues'] if item['id'] == issue_id and item['category'] == 'Supplies'), None)
        if not issue:
            raise ValueError('Supply request no longer exists; refresh the page')
        plan = None
        if 'purchase_plan' in data:
            from purchase_plans import build
            binding = data['purchase_plan']
            if not isinstance(binding, dict) or type(binding.get('version')) is not int:
                raise ValueError('Invalid purchase plan binding')
            message = next((item for item in issue['evidence'] if item['id'] == binding.get('message_id') and item['sender_type'] == 'guest'), None)
            if not message:
                raise ValueError('The selected guest text is no longer part of this request')
            plan = build(db, issue, message)
            if not plan['saved_at'] or plan['stale'] or plan['blockers'] or plan['version'] != binding['version'] or plan['source_fingerprint'] != binding.get('source_fingerprint'):
                raise ValueError('The purchase plan changed. Review and save it again before recording the quote.')
            if offer is None or data.get('fulfillment_version') != plan['destination']['version']:
                raise ValueError('The quote must be bound to the plan’s delivery details')
            essentials = {item['asin'] for item in plan['lines']}
            extras = set(offer['asins']) - essentials
            if not essentials.issubset(offer['asins']) or (extras and (not plan['gift']['requested'] or gift == 0 or len(extras) != 1)) or (gift > 0 and not extras):
                raise ValueError('Quote ASINs must match the saved essentials, plus one optional gift when its cost is included. Update the plan before changing products.')
            offer['purchase_plan'] = {'message_id': message['id'], 'version': plan['version'], 'lines': plan['lines'], 'order_lines': plan['order_lines'], 'gift_requested': plan['gift']['requested']}
        if 'fulfillment_version' in data:
            from fulfillment import profile, quantities
            if len(issue['property_ids']) != 1 or offer is None:
                raise ValueError('A delivery profile requires a uniquely mapped unit and an offer')
            destination = profile(db, issue['property_ids'][0])
            if not destination['verified'] or type(data['fulfillment_version']) is not int or data['fulfillment_version'] != destination['version']:
                raise ValueError('Delivery profile changed or needs confirmation; reload before proposing')
            if offer['location'] != destination['address']['city'] + ', ' + destination['address']['region'] or offer['timezone'] != destination['address']['timezone']:
                raise ValueError('Quote location must match the confirmed unit profile. Update the profile and recheck the offer before proposing.')
            offer['fulfillment'] = {key: destination[key] for key in ('property_id', 'address', 'instructions', 'hand_towel_quantity', 'version', 'verified_at')}
            if dt.date.fromisoformat(offer['arrival_date']) < dt.datetime.now(ZoneInfo(offer['timezone'])).date():
                raise ValueError('Arrival date has passed in the property time zone')
            offer['supply_quantities'] = [{key: item[key] for key in ('item_type', 'quantity', 'unit', 'basis')} for item in plan['lines']] if plan else quantities(issue, destination)
            towels = next((item for item in offer['supply_quantities'] if item['item_type'] == 'hand_towels'), None)
            if towels:
                offer['hand_towels_needed'] = towels['quantity']
        if db.execute('SELECT 1 FROM manual_order_notices WHERE issue_id = ?', (issue_id,)).fetchone():
            raise ValueError('A manual order notice already exists. Check its email status before placing an order.')
        if db.execute("SELECT id FROM purchase_requests WHERE issue_id = ? AND decision != 'vetoed'", (issue_id,)).fetchone():
            raise ValueError('A proposal already exists. Cancel it before changing the items or total.')
        identifier = str(uuid.uuid4())
        db.execute("INSERT INTO purchase_requests (id, issue_id, property, summary, total_cents, gift_cents, decision, notice_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?)", (identifier, issue_id, issue['property'], summary.strip(), total, gift, timestamp, timestamp))
        if offer is not None:
            db.execute('UPDATE purchase_requests SET offer = ? WHERE id = ?', (json.dumps(offer), identifier))
        action = 'purchase_proposed'
        details = {'total_cents': total, 'gift_cents': gift, 'simulation': True}
    else:
        match = re.fullmatch(r'/api/purchases/([a-f0-9-]{36})/(approve|veto)', path)
        if not match:
            raise ValueError('Unknown purchase action')
        identifier, action = match.groups()
        row = db.execute('SELECT * FROM purchase_requests WHERE id = ?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Purchase proposal not found')
        if row['decision'] == 'vetoed':
            raise ValueError('This proposal was cancelled')
        if action == 'approve' and row['decision'] == 'approved':
            raise ValueError('This proposal does not need another approval')
        db.execute('UPDATE purchase_requests SET decision = ?, updated_at = ? WHERE id = ?', ('approved' if action == 'approve' else 'vetoed', timestamp, identifier))
        details = {'simulation': True}
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (identifier, action, timestamp, json.dumps(details)))
    return {'id': identifier}




def deliver_notices(connect, stop):
    while not stop.is_set():
        settings = email_settings()
        if email_ready(settings):
            from manual_orders import send_ready
            try:
                send_ready(connect, settings)
            except Exception as error:
                logging.getLogger(__name__).warning('Manual order scan failed (%s)', type(error).__name__)
            with connect() as db:
                rows = db.execute("SELECT * FROM purchase_requests WHERE decision != 'vetoed' AND (notice_status = 'pending' OR (notice_status = 'sent' AND notified_at IS NULL)) ORDER BY created_at LIMIT 10").fetchall()
            for row in rows:
                if stop.is_set():
                    return
                try:
                    if row['notice_status'] == 'pending':
                        with connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            current = db.execute('SELECT * FROM purchase_requests WHERE id = ?', (row['id'],)).fetchone()
                            if current['decision'] == 'vetoed' or current['notice_status'] != 'pending':
                                continue
                            payload = json.loads(current['email_payload']) if current['email_payload'] else None
                            if payload is None:
                                for name in ('NOTIFICATION_FROM', 'MANAGER_EMAIL'):
                                    if not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', settings[name]):
                                        raise ValueError('Notification addresses must be plain email addresses')
                                from settings import manager_url
                                text = '\n'.join(('Please place this order in Amazon Business.', 'Proposal ID: ' + row['id'], 'Property: ' + row['property'], 'Selected items: ' + row['summary'], 'Recorded all-in total: $' + format(row['total_cents'] / 100, '.2f'), 'Gift included: $' + format(row['gift_cents'] / 100, '.2f'), 'Check the unit, current cost and delivery window before checkout.', 'Open Guest Messages:', manager_url() + '/?purchase=' + row['id']))
                                if current['offer']:
                                    offer = json.loads(current['offer'])
                                    text += '\n' + '\n'.join(('Delivery location: ' + offer['location'], 'Amazon arrival estimate: ' + offer['arrival_date'] + ' ' + offer['delivery_window'] + ' (' + offer['timezone'] + ')', 'Estimate recorded: ' + offer['recorded_at'], offer['source'], *('https://www.amazon.com/dp/' + asin for asin in offer['asins']), 'Confirm price, compatibility, and delivery at checkout.'))
                                payload = {'from': settings['NOTIFICATION_FROM'], 'to': [settings['MANAGER_EMAIL']], 'subject': 'Action required: place Amazon supply order $' + format(row['total_cents'] / 100, '.2f'), 'text': text}
                                db.execute('UPDATE purchase_requests SET email_payload = ? WHERE id = ?', (json.dumps(payload), row['id']))
                            if settings['EMAIL_PROVIDER'] == 'gmail':
                                db.execute("UPDATE purchase_requests SET notice_status = 'sent', email_id = 'smtp:inflight' WHERE id = ?", (row['id'],))
                        if dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(row['created_at']) >= dt.timedelta(hours=23):
                            raise ValueError('Notice expired; cancel and recreate the proposal')
                        result = send_email(settings, payload, 'purchase-notice/' + row['id'])
                        email_id = result['id']
                        if not email_id.startswith('smtp:'):
                            email_id = str(uuid.UUID(email_id))
                        with connect() as db:
                            db.execute("UPDATE purchase_requests SET email_id = ?, notice_status = 'sent', notified_at = ? WHERE id = ?", (email_id, result.get('accepted_at'), row['id']))
                            db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_sent', dt.datetime.now(dt.UTC).isoformat(), '{}'))
                    else:
                        if (row['email_id'] or '').startswith('smtp:'):
                            raise ValueError('Gmail send outcome is unknown; purchase remains blocked')
                        result = resend('emails/' + str(uuid.UUID(row['email_id'])), settings)
                        event = result['last_event']
                        timestamp = dt.datetime.now(dt.UTC).isoformat()
                        with connect() as db:
                            if event in ('delivered', 'opened', 'clicked'):
                                db.execute("UPDATE purchase_requests SET notice_status = 'delivered', notified_at = ?, updated_at = ? WHERE id = ? AND notified_at IS NULL", (timestamp, timestamp, row['id']))
                                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_delivered', timestamp, '{}'))
                            elif event in ('bounced', 'failed', 'suppressed', 'complained'):
                                db.execute("UPDATE purchase_requests SET notice_status = 'failed', error = 'Email delivery failed', updated_at = ? WHERE id = ?", (timestamp, row['id']))
                                db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_failed', timestamp, '{}'))
                except Exception as error:
                    logging.getLogger(__name__).error(json.dumps({'event': 'purchase_notice_failed', 'request_id': row['id'], 'error_type': type(error).__name__}), exc_info=(RuntimeError, RuntimeError(type(error).__name__), error.__traceback__))
                    message = 'Gmail send outcome is unconfirmed. It may have arrived; check before resending. Purchase remains blocked.' if settings['EMAIL_PROVIDER'] == 'gmail' else 'Email not confirmed. Purchase remains blocked; check email configuration or cancel and recreate the notice.'
                    with connect() as db:
                        db.execute("UPDATE purchase_requests SET notice_status = 'failed', error = ? WHERE id = ?", (message, row['id']))
                        db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (row['id'], 'manager_email_failed', dt.datetime.now(dt.UTC).isoformat(), '{}'))
                if stop.wait(0.6):
                    return
        stop.wait(15)
