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
        + '<label><input type="checkbox" name="guest_access_confirmed" value="yes" required> Guest agreed to this entry window</label>'
        + '<button type="submit">Create Turnover task</button></form>'
    )
