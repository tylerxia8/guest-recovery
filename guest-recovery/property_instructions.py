import datetime as dt
import hashlib
import json
import re

from reply_library import (
    QUESTIONS,
    answer_fragments,
    decision_request,
    entities,
    intents,
    lookup,
    missing_details,
    question_parts,
    retrieve,
)


def source_fingerprint(issue):
    return hashlib.sha256(json.dumps({key: issue[key] for key in ('messages', 'property_ids', 'reservation_id', 'check_in', 'check_out')}, sort_keys=True).encode()).hexdigest()


def solution(db, issue, message):
    result = {'message_id': message['id'], 'answers': [], 'missing': [], 'body': '', 'library': [], 'guide_versions': []}
    if len(issue['property_ids']) != 1:
        result['missing'] = ['Choose the property so its previous answers can be used.']
        return result
    property_id = issue['property_ids'][0]
    from stay_adjustments import assessments, effective_stay
    from stay_policy import EASTERN
    policies = assessments(db, issue, message, dt.datetime.now(dt.UTC).isoformat())
    policy_parts = {policy['part']: policy for policy in policies}
    result['stay_requests'] = [{'request_id': item['request_id'], 'decision': item['decision'], 'reason': item['reason'], 'scheduler_approved': item['scheduler_approved']} for item in policies]
    parts = question_parts(message['body'])
    requested = list(dict.fromkeys(intent for part in parts for intent in intents(part)))
    libraries = {intent: lookup(db, property_id, intent) for intent in requested}
    result['library'] = [{'intent': intent, 'question': QUESTIONS[intent], **matches, 'candidates': []} for intent, matches in libraries.items()]
    for part in parts:
        if part in policy_parts:
            policy = policy_parts[part]
            result['answers'].append({'topic': policy['kind'], 'label': 'Check-in and checkout policy', 'body': policy['body'], 'source': 'Property manager’s stay policy', 'source_created_at': policy['scheduler_checked_at'] or message['created_at']})
            if policy['decision'] == 'pending' and policy['reason'] != 'policy_information':
                result['missing'].append(policy['error'] or 'Cleaning readiness and schedule availability must be confirmed before approving this change.')
            continue
        topics = intents(part)
        checkout_time = topics == ['checkout_time']
        checkin_time = topics == ['checkin'] and re.search(r'\b(?:when|what time|check[ -]?in time)\b', part, re.IGNORECASE) and not re.search(r'\b(?:code|key|instructions|access)\b', part, re.IGNORECASE)
        if checkout_time or checkin_time:
            bounds = effective_stay(db, issue)
            key = 'effective_check_out' if checkout_time else 'effective_check_in'
            value = dt.datetime.fromisoformat(bounds[key]).astimezone(EASTERN)
            if checkout_time:
                value = max(value, dt.datetime.combine(value.date(), dt.time(10), EASTERN))
            label = 'Checkout' if checkout_time else 'Check-in'
            body = label + ' is at ' + value.strftime('%-I:%M %p') + ' Eastern Time.'
            result['answers'].append({'topic': topics[0], 'label': label + ' time', 'body': body, 'source': 'Booked time and confirmed stay adjustments', 'source_created_at': message['created_at']})
            continue
        approved = []
        for intent in topics:
            saved = libraries.get(intent, {}).get('approved')
            if not saved or missing_details(part, saved['body']):
                continue
            if intent in {'wifi', 'access', 'checkin', 'fireplace', 'tv', 'amenities', 'appliances', 'climate', 'house_rules', 'local'}:
                if not saved['question'] or not entities(part).issubset(entities(saved['question'])):
                    continue
                from instruction_search import query_embedding
                similarity = float(query_embedding(part).dot(query_embedding(saved['question'])))
                if similarity < 0.85:
                    continue
            approved.append(saved)
        approved_topics = {answer['intent'] for answer in approved} if not decision_request(part) else set()
        for answer in approved:
            if answer['intent'] not in approved_topics:
                continue
            result['answers'].append({'topic': answer['intent'], 'label': QUESTIONS[answer['intent']], 'body': answer['body'], 'source': 'Approved answer for this property', 'source_created_at': answer['updated_at']})
            result['guide_versions'].append({'property_id': property_id, 'intent': answer['intent'], 'version': answer['version']})
        if topics and approved_topics == set(topics):
            continue
        candidates = retrieve(db, property_id, part, issue.get('conversation_id'), message['id'], (issue['check_in'], issue['check_out']))
        if not candidates:
            result['missing'].append('Confirm the stay change or exception with the manager.' if decision_request(part) else 'No matching manager answer found for: ' + part[:180])
            continue
        for library in result['library']:
            if library['intent'] in topics:
                library['candidates'] = [{'body': candidate['body'], 'question': candidate['question'], 'support_count': 1, 'latest_at': candidate['source_created_at']} for candidate in candidates]
        previous = candidates[0]
        body = previous['body']
        if approved_topics:
            body = '\n'.join(fragment for fragment in answer_fragments(body) if not set(intents(fragment)) & approved_topics)
            if not body:
                result['missing'].extend('Please confirm: ' + QUESTIONS[topic] for topic in topics if topic not in approved_topics)
                continue
        unresolved = missing_details(part, body)
        answered_topics = set(intents(body))
        if len(topics) > 1:
            for topic in topics:
                if topic not in approved_topics and topic not in answered_topics and topic.split('_')[0] not in {item.split('_')[0] for item in answered_topics}:
                    unresolved.append(QUESTIONS[topic])
        for label in unresolved:
            result['missing'].append('Please confirm: ' + label)
        result['answers'].append({'topic': topics[0] if topics else 'historical', 'label': 'Suggested reply', 'body': body, 'source': 'Manager’s answer in this conversation' if previous['same_question'] else 'Manager’s answer to a similar question at this property', 'source_message_id': previous['source_message_id'], 'source_message_ids': previous['source_message_ids'], 'source_created_at': previous['source_created_at'], 'previous_question': previous['question'], 'similarity': previous['similarity'], 'relevance': previous['relevance']})
    unique = {}
    for answer in result['answers']:
        unique.setdefault(answer['body'], answer)
    result['answers'] = list(unique.values())
    result['missing'] = list(dict.fromkeys(result['missing']))
    result['body'] = '\n\n'.join(unique)
    if len(result['body']) > 3000:
        result['body'] = ''
        result['missing'].append('Shorten the combined reply before sending.')
    return result


def validated_answer(body):
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 2000:
        raise ValueError('Enter an answer of 1 to 2000 characters')
    if re.search(r'@|https?://|\b(?:\+?\d[\d ().-]{7,}\d|\d{4,}|\d{1,2}[/-]\d{1,2}|today|tomorrow|tonight|yesterday|password|passcode|keypad|lockbox|pin|code|refund|discount|ordered|purchased|gas leak|early check.?in|late check.?out)\b', body, re.IGNORECASE) or re.match(r'^\s*(?:hi|hey|hello)\b', body, re.IGNORECASE):
        raise ValueError('Save general property facts only, without guest names, greetings, contacts, access details, or one-stay exceptions')
    return body.strip()


def save(db, data, timestamp):
    from research import analyze
    row = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    if not row:
        raise ValueError('Refresh Hospitable before saving a reusable answer')
    issue = next((item for item in analyze(json.loads(row['body']), db)['issues'] if item['id'] == data.get('issue_id') and item['category'] == 'Instructions'), None)
    message = next((item for item in issue['evidence'] if item['id'] == data.get('message_id')), None) if issue else None
    if not message or len(issue['property_ids']) != 1:
        raise ValueError('Choose an instruction question mapped to one property')
    fingerprint = source_fingerprint(issue)
    if data.get('source_fingerprint') != fingerprint:
        raise ValueError('The conversation changed. Refresh before approving this answer')
    intent = data.get('intent')
    if not isinstance(intent, str) or intent not in [item['intent'] for item in solution(db, issue, message)['library']]:
        raise ValueError('Choose a supported recurring question from this conversation')
    body = validated_answer(data.get('body'))
    if data.get('approved') is not True:
        raise ValueError('Approve this exact answer for future similar questions at this property')
    property_id = issue['property_ids'][0]
    current = lookup(db, property_id, intent)['approved']
    version = current['version'] if current else 0
    if type(data.get('version')) is not int or data['version'] != version:
        raise ValueError('This property answer changed. Refresh before saving')
    db.execute('INSERT INTO property_answers (property_id, intent, body, version, updated_at, question) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(property_id, intent) DO UPDATE SET body = excluded.body, version = excluded.version, updated_at = excluded.updated_at, question = excluded.question', (property_id, intent, body.strip(), version + 1, timestamp, next(part for part in question_parts(message['body']) if intent in intents(part))))
    db.execute('INSERT INTO audit (incident_id, action, at, details) VALUES (?, ?, ?, ?)', (issue['id'], 'property_answer_approved', timestamp, json.dumps({'property_id': property_id, 'intent': intent, 'version': version + 1, 'body_hash': hashlib.sha256(body.strip().encode()).hexdigest()})))
    return {'saved': True, 'sent': False, 'version': version + 1}
