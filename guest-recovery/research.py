import datetime as dt
import re

from catalog import ITEMS
from reply_library import question_parts
from safety import detect as safety_kind

RULES = (
    ('Safety', r'\b(smoke (?:coming|detected|filling)|smoke alarm (?:sounding|going off)|(?:smell|smelling) (?:of )?gas|gas leak|sparks|exposed wir\w*|carbon monoxide alarm|break.in)\b', 'Notify the property manager immediately. Give guests emergency instructions and tell them to call 911 from a safe location for an active hazard.', 'Manager intervention', 'Urgent'),
    ('Maintenance', r'\b(broken|not working|stopped (?:working|connecting)|no longer (?:works?|working|connects?)|(?:isn.t|aren.t|wasn.t|weren.t) working|(?:doesn.t|won.t|can.t|cannot) (?:work|connect)|can.t.{0,35}\bconnect|neither.{0,60}\bconnect|no hot water|leak\w*|off the wall|clogged)\b', 'Ask the manager to inspect the reported fault and arrange a repair. Confirm the exact item before considering a replacement.', 'Maintenance visit', 'High'),
    ('Cleaning', r'\b(dirty|unclean|stained|mold|mould|filthy|bedbugs?|cockroach\w*)\b', 'Review the photos or conversation, arrange a cleaner if needed, and add the issue to the turnover checklist.', 'Cleaning follow-up', 'Normal'),
    ('Supplies', r'\b(towels?|toilet paper|soap|shampoos?|(?<!air )conditioners?|salt|pepper|(?:cooking|olive) oil|blankets?|pillows?|batter(?:y|ies)|remotes?|laundry|trash bags?|garbage bags?|dish pods?|dishwasher pods?|dishwasher tablets?|detergent)\b', 'Check onsite stock and the full conversation. If still needed, propose a suitable replacement with an all-in budget and verified arrival before checkout.', 'Possible small purchase', 'Normal'),
    ('Instructions', r'\b(check.?out|check.?in|trash|garbage|wifi|wi-fi|parking|gaslog|fireplace)\b', 'Verify the property-specific instructions and prepare a clear answer for the manager to review. Add recurring questions to the guest guide.', 'Guest guide improvement', 'Normal'),
)
ARRIVAL_DEPARTURE = re.compile(r'\b(?:check(?:ing)?[ -]?(?:in|out)|early arrival|late departure|arrival instructions|departure instructions|(?:door|building|gate|access|entry|lock) code|keypad|lockbox|locked out|code to (?:get in|enter)|key[ /-]?code|(?:enter|get in(?:to)?|access) (?:the |our )?(?:house|home|property|unit|building)|which door)\b', re.IGNORECASE)
SIGNAL = re.compile(r'\b(no|not|missing|broken|stopped|dirty|cannot|can.t|don.t (?:have|see|find)|doesn.t|won.t|wasn.t|weren.t|isn.t|aren.t|neither|out of|please|need|bring|can we|could we|can you|could you|how|where|question|instructions|off the wall|permitted)\b', re.IGNORECASE)
TOPICS = {key: value[1] for key, value in ITEMS.items()} | {
    'wifi': r'\b(?:wi-?fi|internet|network|hot ?spot)\b',
    'tv': r'\b(?:tv|television)\b',
    'access': r'\b(?:keypad|lock|door|locked out)\b',
    'hot_water': r'\bhot water\b',
    'heating': r'\b(?:heat|heating|heater)\b',
    'cooling': r'\b(?:air condition\w*|ac|a/c)\b',
    'toilet': r'\btoilet\b',
    'shower': r'\bshower\b',
}
RESOLUTION = re.compile(r'\b(?:found|located|fixed|resolved|sorted|got.{0,20}\bworking|(?:got|getting).{0,20}\bto work|(?:works?|working|connects?|connected) now|now (?:works?|working|connects?|connected)|all (?:good|set))\b', re.IGNORECASE)
NEGATED_RESOLUTION = re.compile(r'\b(?:not|never|still|cannot|can.t|couldn.t|isn.t|is not|hasn.t|haven.t|didn.t|doesn.t|don.t|won.t|without)\b', re.IGNORECASE)


def analyze(snapshot, db=None, *, current=None):
    issues = []
    current = current or dt.datetime.now(dt.UTC)
    active = set(snapshot.get('active_reservation_ids', []))
    upcoming = set(snapshot.get('upcoming_reservation_ids', []))
    for conversation in snapshot['conversations']:
        if db is not None:
            from stay_adjustments import effective_stay
            conversation = {**conversation, **effective_stay(db, conversation)}
        messages = sorted(conversation['messages'], key=lambda message: message['created_at'])
        grouped = {}
        for index, message in enumerate(messages):
            text = message['body'].replace('\u2019', "'")
            if message['sender_type'] != 'guest':
                continue
            clauses = re.split(r'[.!?;\n]+|\bbut\b', text, flags=re.IGNORECASE)
            for category, pattern, remedy, route, priority in RULES:
                candidates = [clause for clause in clauses if safety_kind(clause)] if category == 'Safety' else [clause for clause in clauses if (re.search(pattern, clause, re.IGNORECASE) or category == 'Instructions' and '?' in text) and (category == 'Instructions' or SIGNAL.search(clause))]
                if category == 'Instructions':
                    candidates = [clause for clause in candidates if '?' in text or re.search(r'\b(how|where|what|when|can|could|please|need|send|provide|directions|rules|password)\b', clause, re.IGNORECASE) or re.fullmatch(r'\s*(?:check.?in instructions|check.?out instructions|parking|wi-?fi(?: password)?|trash(?: instructions| rules)?)\s*', clause, re.IGNORECASE)]
                    candidates = [clause for clause in candidates if not re.match(r'^\s*(?:thanks|thank you)\b', clause, re.IGNORECASE) or re.search(r'\b(how|where|what|when|can|could|please|need)\b', clause, re.IGNORECASE)]
                if category == 'Supplies':
                    candidates = [clause for clause in candidates if not re.search(r'\b((?:do not|don.t|no longer|never) need|not missing|have enough|no need)\b', clause, re.IGNORECASE) and re.search(r'\b(no|not|don.t (?:have|see|find)|missing|broken|weren.t|aren.t|out of|need|bring|can we|could we|can you|could you|please)\b', clause, re.IGNORECASE)]
                if not candidates:
                    continue
                issue = grouped.setdefault(category, {'id': conversation['reservation_id'] + ':' + category.lower(), 'category': category, 'priority': priority, 'route': route, 'recommendation': remedy, 'evidence': [], 'resolution_hint': None, 'hand_towel': False, 'item_types': [], 'topics': [], 'problem_types': {}})
                issue['evidence'].append(message)
                issue['problem_types'][message['id']] = list(dict.fromkeys('Check-in & checkout' if ARRIVAL_DEPARTURE.search(part) else 'Instructions' for part in question_parts(text))) if category == 'Instructions' else [category]
                for topic, topic_pattern in TOPICS.items():
                    if topic not in issue['topics'] and any(re.search(topic_pattern, clause, re.IGNORECASE) for clause in candidates):
                        issue['topics'].append(topic)
                if category == 'Supplies':
                    for key, (_, item_pattern, _) in ITEMS.items():
                        if key not in issue['item_types'] and any(re.search(item_pattern, clause, re.IGNORECASE) for clause in candidates):
                            issue['item_types'].append(key)
                if category == 'Supplies' and any(re.search(r'\bhand towels?\b', clause, re.IGNORECASE) for clause in candidates):
                    issue['hand_towel'] = True
                    for clause in candidates:
                        quantity = re.search(r'\b(?:need|bring|send|provide|request|can we have|could we have|can we get|could we get)\s+(?:us\s+)?([1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:(?:extra|additional|more)\s+)?hand towels?\b', clause, re.IGNORECASE)
                        if quantity:
                            number = quantity[1].lower()
                            issue['requested_hand_towels'] = int(number) if number.isdigit() else ('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten').index(number) + 1
        for issue in grouped.values():
            topics = set(issue['topics'])
            if 'wifi' in topics:
                topics.discard('tv')
            if 'toilet_paper' in topics:
                topics.discard('toilet')
            last = issue['evidence'][-1]
            later_guests = [message for message in messages if message['sender_type'] == 'guest' and message['created_at'] > last['created_at']]
            latest_complaint = max(item['evidence'][-1]['created_at'] for item in grouped.values())
            if issue['category'] != 'Safety' and len(topics) == 1:
                topic = next(iter(topics))
                for later in later_guests:
                    for clause in re.findall(r'[^.!?;\n]+[.!?;]?', later['body'].replace('\u2019', "'")):
                        clause = re.sub(r'^\s*never mind\b', '', clause, flags=re.IGNORECASE)
                        if '?' in clause or re.search(r'\b(?:if|when|would|could|should|will|please|hope|think|wonder|maybe)\b', clause, re.IGNORECASE):
                            continue
                        if issue['category'] != 'Supplies' and re.search(r'\b(?:found|located)\b', clause, re.IGNORECASE):
                            continue
                        if not RESOLUTION.search(clause) or NEGATED_RESOLUTION.search(clause):
                            continue
                        mentioned = {key for key, pattern in TOPICS.items() if re.search(pattern, clause, re.IGNORECASE)}
                        if 'wifi' in mentioned:
                            mentioned.discard('tv')
                        if 'toilet_paper' in mentioned:
                            mentioned.discard('toilet')
                        explicit = mentioned == {topic}
                        contextual = not mentioned and later is later_guests[0] and last['created_at'] == latest_complaint and re.search(r'\b(?:it|that|all good|all set)\b', clause, re.IGNORECASE)
                        if explicit or contextual:
                            issue['resolution_hint'] = later
            issue.update({'property': conversation['property'], 'reservation_id': conversation['reservation_id'], 'check_in': conversation['check_in'], 'check_out': conversation['check_out'], 'messages': messages, 'latest_at': issue['evidence'][-1]['created_at']})
            issue['property_ids'] = conversation.get('property_ids', [])
            issue['conversation_id'] = conversation.get('conversation_id')
            issue['source_messages_fingerprint'] = conversation.get('source_messages_fingerprint')
            if issue['hand_towel'] and 'hand_towels' not in issue['item_types']:
                issue['item_types'].append('hand_towels')
            issue['recorded_status'] = 'Likely resolved' if issue['resolution_hint'] else 'Needs review'
            issue['status'] = 'Needs review'
            issue['effective_check_in'] = conversation.get('effective_check_in', conversation['check_in'])
            issue['effective_check_out'] = conversation.get('effective_check_out', conversation['check_out'])
            issue['stay_adjustment_ids'] = conversation.get('stay_adjustment_ids', [])
            check_in = dt.datetime.fromisoformat(issue['effective_check_in'])
            check_out = dt.datetime.fromisoformat(issue['effective_check_out'])
            issue['stay_phase'] = 'upcoming' if current < check_in else 'in_house'
            issue['lane'] = 'likely_resolved' if issue['resolution_hint'] else 'needs_attention'
            live = conversation['reservation_id'] in active and check_in <= current < check_out
            prearrival = conversation['reservation_id'] in upcoming and current < check_in <= current + dt.timedelta(days=2) and issue['category'] == 'Instructions'
            if not live and not prearrival:
                issue['lane'] = 'practice'
            issues.append(issue)
    issues.sort(key=lambda issue: issue['latest_at'], reverse=True)
    return {'issues': issues, 'synced_at': snapshot['synced_at'], 'property_count': snapshot['property_count'], 'reservation_count': len(snapshot['conversations']), 'guest_message_count': sum(message['sender_type'] == 'guest' for conversation in snapshot['conversations'] for message in conversation['messages']), 'nontext_message_count': snapshot['nontext_message_count'], 'scope': snapshot['scope']}
