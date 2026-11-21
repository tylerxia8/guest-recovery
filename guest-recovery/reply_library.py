import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from instruction_search import MODEL_VERSION

INDEX_VERSION = MODEL_VERSION + ':question-parts-v1'

QUESTIONS = {
    'historical': 'Similar guest questions',
    'trash_location': 'Where should trash and recycling go?',
    'trash_collection': 'When is trash collected?',
    'trash_rules': 'What are the trash and recycling instructions?',
    'checkout_steps': 'What are the checkout instructions?',
    'checkout_time': 'What time is checkout?',
    'parking_location': 'Where should vehicles be parked?',
    'parking_overflow': 'Where is additional parking available?',
    'parking_capacity': 'How many vehicles fit in the parking area?',
    'wifi': 'Wi-Fi and internet',
    'access': 'Getting into the property',
    'checkin': 'Check-in instructions',
    'fireplace': 'Fireplace instructions',
    'tv': 'TV instructions',
    'amenities': 'Items in the home',
    'appliances': 'Using the appliances',
    'climate': 'Heating and air conditioning',
    'house_rules': 'House rules',
    'local': 'The surrounding area',
}
TOPIC = {
    'trash': r'\b(?:trash|garbage|recycl\w*|rubbish|dumpster|bins?|city cans?)\b',
    'checkout': r'\bcheck[ -]?out\b',
    'parking': r'\b(?:parking|park(?:ed)?|driveway|garage|vehicles?|cars?|trucks?)\b',
    'wifi': r'\b(?:wi[ -]?fi|internet|ssid|network|bandwidth|mbps)\b',
    'access': r'\b(?:keypad|lockbox|access|entrance|entry|enter|door code|key code|codes?|keys?|which (?:side|door|unit))\b',
    'checkin': r'\bcheck[ -]?in\b',
    'fireplace': r'\b(?:fireplace|gas[ -]?logs?)\b',
    'tv': r'\b(?:tv|television|cable|roku|netflix|streaming|channels?)\b',
    'amenities': r'\b(?:vacuum|iron|ironing|hair ?dryer|crib|pack.{0,3}play|high ?chair|bedding|blankets?|pillows?|linens?|sofa bed|towels?)\b',
    'appliances': r'\b(?:coffee|keurig|washer|dryer|washing|dishwasher|oven|stove|microwave|refrigerator|fridge|ice maker|disposal|shower)\b',
    'climate': r'\b(?:thermostat|heat|heating|cooling|air conditioning|air conditioner|ac|a/c)\b',
    'house_rules': r'\b(?:pets?|dogs?|cats?|smok\w*|visitors?|parties|quiet hours|guests allowed)\b',
    'local': r'\b(?:nearby|restaurants?|walking distance|transit|train|expo|distance|how far)\b',
}
CREDENTIALS = re.compile(r'\b(?:password|passcode|keypad|lockbox|pin|(?:door|access|entry|lock|building|gate) code|code (?:is|will be)|ssid)\b', re.IGNORECASE)
DECISION = re.compile(r'\b(?:later check[ -]?out|latest.{0,20}check[ -]?out|check[ -]?out.{0,15}(?:later|at \d)|check[ -]?in.{0,15}at \d|early check[ -]?in|check[ -]?in (?:early|earlier|before)|late check[ -]?out|check[ -]?out (?:late|later|after)|extend\w*|extension|alteration|refund\w*|discount\w*|reimburse\w*|block the calendar|hold the (?:house|home|property)|add (?:a|another|one|two|three|an extra) (?:night|day)|(?:accept|change|cancel) (?:the |our |my |this )?(?:reservation|booking))\b', re.IGNORECASE)
TIME_SPECIFIC = re.compile(r'\b(?:today|tomorrow|tonight|yesterday|this morning|this afternoon|this evening|this time|this week|next week|this weekend|just for you|for your stay|ordered|purchased|already sent|on (?:my|the) way)\b|\b\d{1,2}[/-]\d{1,2}\b', re.IGNORECASE)
CONTACT = re.compile(r'https?://\S+|\b[\w.+-]+@[\w.-]+\b|(?<!\w)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b', re.IGNORECASE)


def intents(text):
    result = []
    for topic, pattern in TOPIC.items():
        if not re.search(pattern, text, re.IGNORECASE):
            continue
        if topic == 'trash':
            if re.search(r'\b(?:collect\w*|pick[ -]?up|what day|which day|when)\b', text, re.IGNORECASE):
                result.append('trash_collection')
            elif re.search(r'\b(?:where|locat\w*|find)\b', text, re.IGNORECASE):
                result.append('trash_location')
            else:
                result.append('trash_rules')
        elif topic == 'checkout':
            result.append('checkout_time' if re.search(r'\b(?:time|when|hour)\b', text, re.IGNORECASE) else 'checkout_steps')
        elif topic == 'parking':
            if re.search(r'\b(?:extra|additional|overflow|full|taken)\b|\bno\s+(?:parking\s+)?(?:spots?|spaces?)\b', text, re.IGNORECASE):
                result.append('parking_overflow')
            elif re.search(r'\b(?:how many|fit|capacity|maximum)\b|\b\d+\s+(?:cars?|vehicles?|trucks?)\b', text, re.IGNORECASE):
                result.append('parking_capacity')
            else:
                result.append('parking_location')
        else:
            result.append(topic)
    return result


def decision_request(text):
    from stay_policy import EARLY, LATE
    if DECISION.search(text) or EARLY.search(text) or LATE.search(text):
        return True
    if re.search(r'\b(?:can|could|possible)\b.{0,80}\b(?:check[ -]?in|check[ -]?out|arrive|get into)\b', text, re.IGNORECASE):
        return True
    return bool(re.search(r'\b(?:available|availability|vacant|open)\b', text, re.IGNORECASE) and (re.search(r'\b(?:book|booking|stay|rent|reservation|house|home|apartment|property|unit|dates?|night)\b|\b\d{1,2}[/-]\d{1,2}\b', text, re.IGNORECASE) or not intents(text)))


def credential_material(question, answer):
    topics = intents(question)
    return bool(CREDENTIALS.search(question) or CREDENTIALS.search(answer) or re.search(r'\b\d{4,8}\b', answer) or 'wifi' in topics or 'wifi' in intents(answer) or 'access' in topics and re.search(r'\b(?:code|pin|keypad|password)\b|\b\d{3,}\b', question + ' ' + answer, re.IGNORECASE))


def question_key(text):
    text = text.casefold().replace('\u2019', "'")
    text = re.sub(r'^\s*(?:hi|hey|hello|good morning|good afternoon|good evening)\b[^,!?\n]{0,50}[,!\n]+\s*', '', text)
    text = re.sub(r'\b(?:garbage|rubbish)\b', 'trash', text)
    text = re.sub(r'\bcheck[ -]?out\b', 'checkout', text)
    return hashlib.sha256(' '.join(re.findall(r'\w+', text)).encode()).hexdigest()


def search_terms(text):
    ignored = {'a', 'an', 'the', 'this', 'that', 'is', 'are', 'was', 'were', 'be', 'to', 'of', 'for', 'at', 'in', 'on', 'with', 'and', 'or', 'i', 'we', 'you', 'your', 'our', 'my', 'it', 'they', 'have', 'has', 'do', 'does', 'can', 'could', 'would', 'please', 'thanks', 'thank', 'hello', 'hi', 'hey', 'what', 'where', 'when', 'how', 'there', 'me', 'us', 'want', 'need', 'know', 'also', 'just', 'will', 'get'}
    return {word for word in re.findall(r'[a-z]{2,}', text.casefold()) if word not in ignored}


def question_parts(text):
    text = text.replace('\u2019', "'")
    parts = []
    for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z])|\n+', text):
        for part in re.split(r'\band\s+(?=(?:how|where|when|what|which|can|could|do you|does|is there|are there)\b)', sentence, flags=re.IGNORECASE):
            part = part.strip()
            if len(search_terms(part)) < 1:
                continue
            if '?' in part or re.search(r'\b(?:how|where|what|when|which|can we|could we|can you|could you|is there|are there|instructions|guidelines|wondering|question|permitted|allowed)\b', part, re.IGNORECASE):
                parts.append(part)
    return parts or [text.strip()]


def answer_fragments(text):
    text = re.sub(r'^\s*(?:hi|hey|hello|dear)\b[^,!?\n]{0,50}[,!\n]+\s*', '', text.strip(), flags=re.IGNORECASE)
    fragments = []
    credentials = bool(CREDENTIALS.search(text) or re.search(TOPIC['wifi'], text, re.IGNORECASE))
    for part in re.split(r'\n\s*\n|(?<=[.!?])\s+(?=[A-Z])', text):
        part = part.strip()
        if not part or len(part) > 2000 or CONTACT.search(part) or '?' in part:
            continue
        if re.fullmatch(r'(?:thank(?:s| you).*|you.re welcome.*|no problem[!. ]*|sure[!. ]*|yes[!. ]*|okay[!. ]*|ok[!. ]*|let (?:me|us) know.*|best regards.*|regards.*)', part, re.IGNORECASE) and not intents(part):
            continue
        if TIME_SPECIFIC.search(part):
            continue
        if re.search(r"\b(?:i.ve let|we.ve let|they.ll|he.ll|she.ll)\b|\b(?:i|we|i.ll|we.ll)\s+(?:will\s+|can\s+|have\s+|am\s+|are\s+)?(?:send|ask|check|arrange|reach|contact|come|stop by|drop off|bring|schedule|order|purchase|look into)\b|\b(?:photo|picture|attachment|screenshot)\b", part, re.IGNORECASE):
            continue
        if len(search_terms(part)) < 2 and not credentials:
            continue
        fragments.append(part)
    return fragments


def missing_details(question, answer):
    missing = []
    checks = {
        'trash_location': ('the trash-bin location', r'\b(?:side|behind|beside|front|back|next to|located|outside|inside|garage|driveway|street|parking lot)\b'),
        'trash_collection': ('the trash collection day', r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|daily|weekly|each week|every week)\b'),
        'parking_location': ('where to park', r'\b(?:lot|street|garage|driveway|front|back|side|beside|behind|designated|assigned)\b'),
        'parking_capacity': ('the number of parking spaces', r'\b(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b'),
        'parking_overflow': ('additional parking', r'\b(?:extra|additional|overflow|street|public|only|no other|no additional)\b'),
        'checkout_time': ('the checkout time', r'\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?|noon|midnight)\b'),
        'checkout_steps': ('the checkout instructions', r'\b(?:dishes|dishwasher|trash|linens|towels|doors|lights|thermostat|lock|leave|empty|remove)\b'),
    }
    for intent in intents(question):
        if intent in checks:
            label, evidence = checks[intent]
            if not re.search(evidence, answer, re.IGNORECASE):
                missing.append(label)
    for pattern, label, evidence in (
        (r'\b(?:speed|mbps|bandwidth)\b', 'internet speed', r'\b(?:mbps|gbps|speed|bandwidth|fiber|fibre)\b'),
        (r'\bcable\b', 'cable TV', r'\b(?:cable|satellite|channels)\b'),
        (r'\bsmart[ -]?tv\b', 'smart TV', r'\b(?:smart|roku|streaming|netflix)\b'),
    ):
        if re.search(pattern, question, re.IGNORECASE) and not re.search(evidence, answer, re.IGNORECASE):
            missing.append(label)
    if 'fireplace' in intents(question) and re.search(r'\b(?:how|turn on|light|operate|start)\b', question, re.IGNORECASE) and not re.search(r'\b(?:switch|button|knob|pilot|turn|press|instructions|remote)\b', answer, re.IGNORECASE):
        missing.append('how to operate the fireplace')
    return missing


def entities(text):
    patterns = {
        'vacuum': r'vacuum', 'iron': r'\biron(?:ing)?\b', 'hairdryer': r'hair ?dryer', 'crib': r'crib|pack.{0,3}play',
        'bedding': r'bedding|blankets?|pillows?|linens?|sofa bed', 'oven': r'\boven\b', 'stove': r'\bstove\b',
        'dishwasher': r'dishwasher', 'washer': r'(?<!dish)washer|washing machine', 'dryer': r'(?<!hair )\bdryer\b',
        'coffee': r'coffee|keurig', 'fridge': r'fridge|refrigerator|ice maker', 'shower': r'shower',
        'pool': r'\bpool\b', 'yard': r'\byard|fenc\w*', 'pets': r'\b(?:pets?|dogs?|cats?)\b',
    }
    return {key for key, pattern in patterns.items() if re.search(pattern, text, re.IGNORECASE)}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS reply_examples (property_id TEXT NOT NULL, intent TEXT NOT NULL, body TEXT NOT NULL, normalized_body TEXT NOT NULL, question TEXT NOT NULL, source_question_id TEXT NOT NULL, source_message_id TEXT NOT NULL, source_created_at TEXT NOT NULL, conversation_id TEXT NOT NULL, PRIMARY KEY (property_id, intent, source_question_id, source_message_id))')
    db.execute('CREATE INDEX IF NOT EXISTS reply_examples_lookup ON reply_examples (property_id, intent, normalized_body)')
    db.execute('CREATE TABLE IF NOT EXISTS property_answers (property_id TEXT NOT NULL, intent TEXT NOT NULL, body TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (property_id, intent))')
    if 'question_key' not in {row[1] for row in db.execute('PRAGMA table_info(reply_examples)')}:
        db.execute("ALTER TABLE reply_examples ADD COLUMN question_key TEXT NOT NULL DEFAULT ''")
    if 'question' not in {row[1] for row in db.execute('PRAGMA table_info(property_answers)')}:
        db.execute("ALTER TABLE property_answers ADD COLUMN question TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE INDEX IF NOT EXISTS reply_examples_question ON reply_examples (property_id, intent, question_key, source_created_at)')
    db.execute('CREATE TABLE IF NOT EXISTS instruction_exchanges (property_id TEXT NOT NULL, conversation_id TEXT NOT NULL, question_id TEXT NOT NULL, question TEXT NOT NULL, question_at TEXT NOT NULL, answer TEXT NOT NULL, answer_ids TEXT NOT NULL, answered_at TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN (\'property\', \'conversation\')), topics TEXT NOT NULL, embedding BLOB NOT NULL, model_version TEXT NOT NULL, PRIMARY KEY(property_id, conversation_id, question_id))')
    db.execute('CREATE INDEX IF NOT EXISTS instruction_exchanges_property ON instruction_exchanges(property_id, scope, answered_at)')


def lookup(db, property_id, intent, question_text=None):
    if intent not in QUESTIONS:
        raise ValueError('Unsupported property instruction intent')
    cursor = db.execute('SELECT property_id, intent, body, version, updated_at, question FROM property_answers WHERE property_id = ? AND intent = ?', (property_id, intent))
    row = cursor.fetchone()
    approved = dict(zip((column[0] for column in cursor.description), row)) if row else None
    rows = db.execute('SELECT body, COUNT(DISTINCT source_message_id), MAX(source_created_at) FROM reply_examples WHERE property_id = ? AND intent = ? GROUP BY normalized_body ORDER BY MAX(source_created_at) DESC LIMIT 3', (property_id, intent)).fetchall()
    candidates = [{'body': row[0], 'support_count': row[1], 'latest_at': row[2], 'question': QUESTIONS[intent]} for row in rows]
    return {'approved': approved, 'candidates': candidates, 'example_count': sum(row[1] for row in rows), 'matching': None}


@lru_cache(maxsize=2048)
def relevant_excerpt(question, answer):
    from instruction_search import relevance
    fragments = answer_fragments(answer)
    if not fragments:
        return '', None
    labels = [QUESTIONS[intent] for intent in intents(question)]
    if credential_material(question, answer) and set(intents(question)) & {'wifi', 'access'}:
        body = '\n'.join(fragments)
        return body, max(relevance(question, [body]))
    contextual = [bool(re.match(r'^(?:yes|no|they|it|these|those|in the|on the|under|inside|outside|by the|beside)\b', fragment, re.IGNORECASE) and not intents(fragment)) for fragment in fragments]
    documents = [(' '.join(labels) + ': ' if inferred else '') + fragment for fragment, inferred in zip(fragments, contextual)]
    scores = relevance(question, documents)
    query_topics = {topic.split('_')[0] for topic in intents(question)}
    chosen = set()
    for index, (fragment, score) in enumerate(zip(fragments, scores)):
        if re.search(r"\b(?:not sure|don.t know|do not know|looking into|will get back|awaiting|checking with)\b", fragment, re.IGNORECASE):
            continue
        narrow = {'trash_location', 'trash_collection', 'parking_location', 'parking_capacity', 'parking_overflow', 'checkout_time', 'checkout_steps'}
        if len(intents(question)) == 1 and set(intents(question)) & narrow and missing_details(question, fragment):
            continue
        fragment_topics = {topic.split('_')[0] for topic in intents(fragment)}
        factual = bool(re.search(r'\b(?:is|are|located|side|behind|front|back|on|in|allowed|permitted|can|cannot|each|every|must|should|please)\b', fragment, re.IGNORECASE))
        explicit = bool(query_topics & fragment_topics & {'trash', 'parking', 'checkout', 'checkin', 'wifi', 'fireplace', 'access'} and factual and not missing_details(question, fragment))
        location = bool(contextual[index] and re.search(r'\b(?:where|locat\w*|guidelines|instructions)\b', question, re.IGNORECASE) and re.search(r'\b(?:side|behind|beside|front|back|inside|outside|closet|cupboard|cabinet|under|next to)\b', fragment, re.IGNORECASE))
        if score >= 0 or explicit or location:
            chosen.add(index)
    for index in list(chosen):
        for adjacent in (index - 1, index + 1):
            if 0 <= adjacent < len(fragments) and re.match(r'^(?:if|unless|except|however|but|otherwise|only|provided)\b', fragments[adjacent], re.IGNORECASE):
                chosen.add(adjacent)
    body = '\n'.join(fragment for index, fragment in enumerate(fragments) if index in chosen)
    return body, max(scores)


def retrieve(db, property_id, question, conversation_id=None, message_id=None, stay=None):
    rows = db.execute("SELECT conversation_id, question_id, question, answer, answer_ids, answered_at, scope, embedding FROM instruction_exchanges WHERE property_id = ? AND model_version = ? AND (scope = 'property' OR conversation_id = ?)", (property_id, INDEX_VERSION, conversation_id or '')).fetchall()
    if not rows:
        return []
    import numpy as np

    from instruction_search import query_embedding

    query = query_embedding(question)
    matrix = np.stack([np.frombuffer(row[7], dtype=np.float32) for row in rows])
    similarities = matrix @ query
    order = sorted(range(len(rows)), key=lambda index: (float(similarities[index]), rows[index][5]), reverse=True)[:16]
    order = list(dict.fromkeys([*[index for index, row in enumerate(rows) if row[0] == conversation_id and row[1] == message_id], *order]))
    candidates = []
    required = set(intents(question))
    for index in order:
        row = rows[index]
        if credential_material(row[2], row[3]) and row[0] != conversation_id:
            continue
        same_question = row[0] == conversation_id and row[1] == message_id
        if credential_material(row[2], row[3]) and (not stay or not dt.datetime.fromisoformat(stay[0]) - dt.timedelta(days=7) <= dt.datetime.fromisoformat(row[5]) <= dt.datetime.fromisoformat(stay[1])):
            continue
        similarity = float(similarities[index])
        if not same_question and similarity < 0.55:
            continue
        past_topics = set(intents(row[2]))
        if entities(question) and not entities(question).issubset(entities(row[2])):
            continue
        if required and past_topics and not {topic.split('_')[0] for topic in required} & {topic.split('_')[0] for topic in past_topics}:
            continue
        body, relevance = relevant_excerpt(question, row[3])
        if not body:
            continue
        if decision_request(question) and not re.search(r'\b(?:subject to|depend\w*|standard|regular|normally|policy|cannot guarantee|can.t guarantee)\b', body, re.IGNORECASE):
            continue
        candidates.append({'body': body, 'question': row[2], 'source_question_id': row[1], 'source_message_id': json.loads(row[4])[0], 'source_message_ids': json.loads(row[4]), 'source_created_at': row[5], 'conversation_id': row[0], 'similarity': round(similarity, 4), 'relevance': round(relevance, 4), 'same_question': same_question, 'scope': row[6]})
    candidates.sort(key=lambda item: (item['same_question'], round(item['similarity'] + 0.1 / (1 + math.exp(-item['relevance'])), 2), item['source_created_at']), reverse=True)
    return candidates[:3]


def exchanges(messages):
    pending = []
    answers = []
    for message in sorted(messages, key=lambda item: (item['created_at'], item['id'])):
        message_id, sender, created_at, body = (message[key] for key in ('id', 'sender_type', 'created_at', 'body'))
        if not body or not created_at:
            continue
        created = dt.datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            continue
        if pending and created - pending[0]['created'] > dt.timedelta(hours=24):
            if answers:
                for question in pending:
                    yield question, answers
            pending, answers = [], []
        if sender == 'guest':
            if answers:
                for question in pending:
                    yield question, answers
                pending, answers = [], []
            if '?' in body or re.search(r'\b(?:instructions|guidelines|how|where|wondering|question|permitted)\b', body, re.IGNORECASE):
                pending.append({'id': message_id, 'body': body, 'created': created, 'created_at': created_at})
        elif sender == 'host' and pending and message.get('source') in ('platform', 'hospitable') and answer_fragments(body):
            answers.append({'id': message_id, 'body': body, 'created_at': created_at})
    if pending and answers:
        for question in pending:
            yield question, answers


def conversation_records(property_id, conversation_id, messages):
    names = set()
    for message in messages:
        sender = message.get('sender')
        if message['sender_type'] == 'guest' and isinstance(sender, dict):
            names.update(name for key in ('first_name', 'full_name') if isinstance(name := sender.get(key), str) and len(name.strip()) > 1)
    for question, answers in exchanges(messages):
        body = '\n\n'.join(answer['body'] for answer in answers)
        for name in sorted(names, key=len, reverse=True):
            escaped = re.escape(name.strip())
            body = re.sub(r'(^|[\n.!?])\s*(?:(?:hi|hey|hello|dear|sure|thanks|thank you|no problem)[ ,]*)?' + escaped + r'[ ,!:]+', r'\1', body, flags=re.IGNORECASE)
            body = re.sub(r',\s*' + escaped + r'(?=[,.!]|$)', '', body, flags=re.IGNORECASE)
        body = re.sub(r'^(?:sure|no problem)[ ,!]*', '', body.strip(), flags=re.IGNORECASE)
        topics = intents(question['body'])
        if not answer_fragments(body):
            continue
        sensitive = credential_material(question['body'], body)
        scope = 'conversation' if sensitive or decision_request(question['body']) or decision_request(body) else 'property'
        yield (property_id, conversation_id, question['id'], question['body'], question['created_at'], body, json.dumps([answer['id'] for answer in answers]), answers[-1]['created_at'], scope, json.dumps(topics))


def write_examples(db, records):
    for row in records:
        if row[8] != 'property':
            continue
        answer = '\n'.join(answer_fragments(row[5]))
        for intent in json.loads(row[9]):
            db.execute('INSERT OR IGNORE INTO reply_examples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (row[0], intent, answer, hashlib.sha256(answer.encode()).hexdigest(), row[3], row[2], json.loads(row[6])[0], row[7], row[1], question_key(row[3])))


def learn(db, conversations):
    from instruction_search import encode
    records = []
    refreshed = []
    existing = {(row[0], row[1]): row[2:] for row in db.execute('SELECT conversation_id, question_id, question, answer, model_version, embedding FROM instruction_exchanges')}
    embedded = []
    for conversation in conversations:
        conversation_id = conversation.get('conversation_id')
        if not conversation_id:
            continue
        refreshed.append(conversation_id)
        if len(conversation.get('property_ids', [])) != 1:
            continue
        for row in conversation_records(conversation['property_ids'][0], conversation_id, conversation['messages']):
            records.append(row)
            previous = existing.get((conversation_id, row[2]))
            if previous and tuple(previous[:3]) == (row[3], row[5], INDEX_VERSION):
                embedded.append((*row, previous[3], INDEX_VERSION))
            else:
                embedded.append((*row, encode([' '.join(question_parts(row[3]))])[0].tobytes(), INDEX_VERSION))
    for conversation_id in refreshed:
        db.execute('DELETE FROM instruction_exchanges WHERE conversation_id = ?', (conversation_id,))
        db.execute('DELETE FROM reply_examples WHERE conversation_id = ?', (conversation_id,))
    db.executemany('INSERT INTO instruction_exchanges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', embedded)
    write_examples(db, records)
    logging.getLogger(__name__).info(json.dumps({'event': 'instruction_library_learned', 'conversations': len(refreshed), 'exchanges': len(records)}))
    return len(records)


def import_archive(archive_path, target_db_path):
    from instruction_search import encode
    archive_path = Path(archive_path).expanduser().resolve(strict=True)
    target_path = Path(target_db_path).expanduser().resolve(strict=True)
    if archive_path == target_path:
        raise ValueError('Archive and application database must be different files')
    records = []
    with closing(sqlite3.connect(target_path, timeout=10)) as target:
        row = target.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
        snapshot_original = row[0] if row else None
        snapshot = json.loads(snapshot_original) if snapshot_original else None
        known = {}
        if target.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'instruction_exchanges'").fetchone():
            known = {(row[0], row[1]): (row[2], row[3]) for row in target.execute('SELECT conversation_id, question_id, question, embedding FROM instruction_exchanges WHERE model_version = ?', (INDEX_VERSION,))}
    counts = {'conversations': 0, 'human_exchanges': 0, 'examples': 0, 'properties': 0}
    with closing(sqlite3.connect(archive_path.as_uri() + '?mode=ro', uri=True, timeout=10)) as archive:
        archive.execute('BEGIN')
        deadline = time.monotonic() + 600
        archive.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        mappings = archive.execute('SELECT rm.conversation_id, MIN(rp.property_id) FROM resource_messages AS rm LEFT JOIN resource_properties AS rp ON rp.source = rm.source AND rp.resource_id = rm.resource_id GROUP BY rm.conversation_id HAVING COUNT(DISTINCT rp.property_id) = 1 AND SUM(rp.property_id IS NULL) = 0').fetchall()
        for conversation_id, property_id in mappings:
            counts['conversations'] += 1
            messages = []
            for message_id, sender, created_at, body, raw in archive.execute('SELECT message_id, sender_type, created_at, body, raw_json FROM messages WHERE conversation_id = ? ORDER BY created_at, message_id', (conversation_id,)):
                source = json.loads(raw)
                messages.append({'id': message_id, 'sender_type': sender, 'created_at': created_at, 'body': body, 'source': source.get('source'), 'sender': source.get('sender')})
            records.extend(conversation_records(property_id, conversation_id, messages))
        if snapshot:
            for conversation in snapshot['conversations']:
                mapping = archive.execute("SELECT conversation_id FROM resources WHERE source = 'reservation' AND resource_id = ?", (conversation['reservation_id'],)).fetchone()
                if not mapping:
                    continue
                conversation['conversation_id'] = mapping[0]
                metadata = {row[0]: json.loads(row[1]) for row in archive.execute('SELECT message_id, raw_json FROM messages WHERE conversation_id = ?', (mapping[0],))}
                for message in conversation['messages']:
                    raw = metadata.get(message['id'], {})
                    if raw:
                        message['source'] = raw.get('source')
                        message['sender'] = raw.get('sender')
        archive.set_progress_handler(None, 0)
    counts['human_exchanges'] = len(records)
    embedded = []
    for offset in range(0, len(records), 128):
        batch = records[offset:offset + 128]
        missing = [row for row in batch if (row[1], row[2]) not in known or known[(row[1], row[2])][0] != row[3]]
        generated = iter(encode([' '.join(question_parts(row[3])) for row in missing]))
        for row in batch:
            previous = known.get((row[1], row[2]))
            vector = previous[1] if previous and previous[0] == row[3] else next(generated).tobytes()
            embedded.append((*row, vector, INDEX_VERSION))
        if len(embedded) % 1024 == 0 or len(embedded) == len(records):
            logging.getLogger(__name__).info(json.dumps({'event': 'instruction_library_embedding', 'completed': len(embedded), 'total': len(records)}))
    backup_directory = Path.home() / '.guest-recovery-backups'
    backup_directory.mkdir(mode=0o700, exist_ok=True)
    if backup_directory.is_symlink():
        raise ValueError('Backup directory must not be a symbolic link')
    backup_directory.chmod(0o700)
    descriptor, backup_path = tempfile.mkstemp(prefix='reply-library-', suffix='.sqlite3', dir=backup_directory)
    os.close(descriptor)
    os.chmod(backup_path, 0o600)
    with closing(sqlite3.connect(target_path, timeout=10)) as target:
        with closing(sqlite3.connect(backup_path, timeout=10)) as backup:
            deadline = time.monotonic() + 60

            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError('Application database backup timed out')

            target.backup(backup, pages=256, progress=progress, sleep=0.1)
        with target:
            target.execute('BEGIN IMMEDIATE')
            initialize(target)
            target.execute('DELETE FROM instruction_exchanges')
            target.executemany('INSERT INTO instruction_exchanges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', embedded)
            target.execute('DELETE FROM reply_examples')
            write_examples(target, records)
            if snapshot:
                target.execute('UPDATE research_snapshot SET body = ? WHERE id = 1 AND body = ?', (json.dumps(snapshot), snapshot_original))
    os.chmod(target_path, 0o600)
    counts['examples'] = len(records)
    counts['properties'] = len({row[0] for row in records})
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive_sqlite')
    parser.add_argument('target_app_sqlite')
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(import_archive(arguments.archive_sqlite, arguments.target_app_sqlite), sort_keys=True))


if __name__ == '__main__':
    main()
