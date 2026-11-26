"""Case details supplied by the manager to Maintenance intake."""

import hashlib
import json
from html import escape
from uuid import UUID

from auth import AuthError


def repair_payload(fields):
    try:
        property_id = str(UUID(fields['property_external_id']))
        booking_id = str(UUID(fields['reservation_external_id']))
        description = fields['description'].strip()
    except (KeyError, ValueError):
        raise AuthError(400, 'Choose a Hospitable stay and describe the repair.') from None
    if not 1 <= len(description) <= 5000:
        raise AuthError(400, 'Describe the repair in 1–5,000 characters.')
    fingerprint = hashlib.sha256(json.dumps([property_id, booking_id, description]).encode()).hexdigest()
    return {
        'issue_id': 'private-repair:' + fingerprint,
        'external_property_id': property_id,
        'reservation_id': booking_id,
        'conversation_id': booking_id,
        'description': description,
        'source_fingerprint': fingerprint,
    }


def repair_form(csrf, query):
    values = {
        'csrf': csrf,
        'property_external_id': query['property_external_id'][0],
        'reservation_external_id': query['reservation_external_id'][0],
    }
    fields = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">' for name, value in values.items()
    )
    return (
        '<form action="/repair-request" method="post" id="repair-request"><h2>Send to Maintenance</h2>' + fields
        + '<label>Repair needed<textarea name="description" maxlength="5000" required></textarea></label>'
        + '<button type="submit">Create repair request</button></form>'
    )


def answer_payload(fields):
    try:
        property_id = str(UUID(fields['property_external_id']))
        booking_id = str(UUID(fields['reservation_external_id']))
        issue = fields['issue_id']
        field = fields['field_key']
        answer = fields['answer'].strip()
    except (KeyError, ValueError):
        raise AuthError(400, 'Open the repair and enter the guest’s answer.') from None
    if not (1 <= len(issue) <= 200 and 1 <= len(field) <= 100 and 1 <= len(answer) <= 5000):
        raise AuthError(400, 'Enter the guest’s answer in 1–5,000 characters.')
    return {
        'issue_id': issue, 'external_property_id': property_id,
        'reservation_id': booking_id, 'field_key': field, 'body': answer,
    }


def answer_form(csrf, payload, question):
    hidden = {
        'csrf': csrf,
        'property_external_id': payload['external_property_id'],
        'reservation_external_id': payload['reservation_id'],
        'issue_id': payload['issue_id'],
        'field_key': question['field_key'],
    }
    fields = ''.join(
        f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'
        for name, value in hidden.items()
    )
    return (
        '<form action="/repair-answer" method="post">' + fields
        + '<label>Guest’s answer<textarea name="answer" maxlength="5000" required></textarea></label>'
        + '<button type="submit">Save answer in Maintenance</button></form>'
    )
