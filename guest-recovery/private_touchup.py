"""Manager-confirmed cleaning handoff inputs for the private inbox."""

import datetime as dt
import json
from html import escape
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from auth import AuthError

EASTERN = ZoneInfo('America/New_York')


def eastern_time(value):
    local = dt.datetime.fromisoformat(value)
    if local.tzinfo is not None:
        raise ValueError('Expected an Eastern local time.')
    first = local.replace(tzinfo=EASTERN, fold=0)
    second = local.replace(tzinfo=EASTERN, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError('Choose a time outside the daylight-saving transition.')
    return first.isoformat()


def cleaning_payload(fields):
    try:
        body = {
            'property_external_id': fields['property_external_id'],
            'reservation_external_id': fields['reservation_external_id'],
            'release_at': eastern_time(fields['release_at']),
            'deadline_at': eastern_time(fields['deadline_at']),
            'scope': fields['scope'].strip(),
            'duration_minutes': int(fields['duration_minutes']),
            'guest_access_confirmed': fields.get('guest_access_confirmed') == 'yes',
        }
    except (KeyError, ValueError) as error:
        raise AuthError(400, 'Enter a valid cleaning scope, duration and Eastern time window.') from error
    if not body['guest_access_confirmed']:
        raise AuthError(400, 'Confirm that the guest agreed to the entry window.')
    body['external_request_id'] = str(uuid5(NAMESPACE_URL, 'lucena:guest-touchup:' + json.dumps(body, sort_keys=True)))
    return body


def cleaning_form(csrf, query):
    hidden = {
        'csrf': csrf,
        'property_external_id': query['property_external_id'][0],
        'reservation_external_id': query['reservation_external_id'][0],
    }
    fields = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'
        for name, value in hidden.items()
    )
    return (
        '<form action="/cleaning-touchup" method="post"><h2>Request a cleaning touch-up</h2>'
        + fields
        + '<label>Cleaning needed<textarea name="scope" maxlength="1000" required></textarea></label>'
        + '<label>Earliest entry (Eastern)<input type="datetime-local" name="release_at" required></label>'
        + '<label>Latest finish (Eastern)<input type="datetime-local" name="deadline_at" required></label>'
        + '<label>Duration (minutes)<input type="number" name="duration_minutes" min="15" max="240" required></label>'
        + '<label class="entry-confirmation"><input type="checkbox" name="guest_access_confirmed" value="yes" required> Guest agreed to this entry window</label>'
        + '<button type="submit">Create Cleaner task</button></form>'
    )


def stay_change_payload(fields):
    try:
        kind = fields['kind']
        property_id = fields['property_external_id']
        reservation_id = fields['reservation_external_id']
        booked_start = dt.datetime.fromisoformat(fields['booked_check_in'])
        booked_end = dt.datetime.fromisoformat(fields['booked_check_out'])
        if (
            kind not in ('early_checkin', 'late_checkout')
            or not 1 <= len(property_id) <= 120
            or not 1 <= len(reservation_id) <= 120
            or booked_start.tzinfo is None
            or booked_end.tzinfo is None
            or booked_start >= booked_end
        ):
            raise ValueError('Invalid booking or stay change')
        body = {
            'kind': kind, 'property_external_id': property_id, 'reservation_external_id': reservation_id,
            'booked_check_in': booked_start.astimezone(dt.UTC).isoformat(),
            'booked_check_out': booked_end.astimezone(dt.UTC).isoformat(),
            'target_at': eastern_time(fields['target_at']),
        }
    except (KeyError, ValueError):
        raise AuthError(400, 'Choose a stay and enter the requested Eastern time.') from None
    body['request_id'] = str(uuid5(NAMESPACE_URL, 'lucena:stay-change:' + json.dumps(body, sort_keys=True)))
    return body


def stay_change_form(csrf, fields, stay_times):
    from private_handoffs import stay_time

    booked = {key: stay_times.get(key) for key in ('booked_check_in', 'booked_check_out')}
    if not all(isinstance(value, str) and value for value in booked.values()):
        return ''
    hidden = {**fields, **booked, 'csrf': csrf}
    inputs = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">' for name, value in hidden.items()
    )
    return (
        '<form action="/stay-change" method="post"><h2>Check-in or checkout request</h2>' + inputs
        + '<p>Booked stay · ' + escape(stay_time(booked['booked_check_in']))
        + ' to ' + escape(stay_time(booked['booked_check_out'])) + '</p>'
        + '<label>Request type<select name="kind" required>'
        + '<option value="early_checkin">Early check-in</option><option value="late_checkout">Late checkout</option>'
        + '</select></label>'
        + '<label>Requested time (Eastern)<input type="datetime-local" name="target_at" required></label>'
        + '<p>Cleaner updates the schedule when the request meets the rules.</p>'
        + '<button type="submit">Check and apply</button></form>'
    )
