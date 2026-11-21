import re
import datetime as dt

from catalog import ITEMS

RULES = (
    ('Safety', r'\b(smoke (?:coming|detected|filling)|smoke alarm (?:sounding|going off)|(?:smell|smelling) (?:of )?gas|gas leak|sparks|exposed wir\w*|carbon monoxide alarm|break.in|(?:door|lock).{0,30}(?:won.t latch|can.t secure)|(?:loose (?:deck )?step|step.{0,30}loose))\b', 'Escalate to the property manager immediately. Confirm the situation and arrange an appropriate emergency or licensed response.', 'Manager intervention', 'Urgent'),
    ('Maintenance', r'\b(broken|not working|stopped (?:working|connecting)|no longer (?:works?|working|connects?)|(?:isn.t|aren.t|wasn.t|weren.t) working|(?:doesn.t|won.t|can.t|cannot) (?:work|connect)|can.t.{0,35}\bconnect|neither.{0,60}\bconnect|no hot water|(?:water is )?pooling under|screen goes black|(?:bedroom|room) is still [7-9][0-9] degrees|leak\w*|off the wall|clogged)\b', 'Ask the manager to inspect the reported fault and arrange a repair. Confirm the exact item before considering a replacement.', 'Maintenance visit', 'High'),
    ('Cleaning', r'\b(dirty|unclean|stained|mold|mould|filthy|(?:hair|crumbs).{0,35}(?:sheets|bedding)|trash was full|(?:dried food|food stuck).{0,35}(?:plates|dishes)|bedbugs?|cockroach\w*)\b', 'Review the photos or conversation, arrange a cleaner if needed, and add the issue to the turnover checklist.', 'Cleaning follow-up', 'Normal'),
    ('Supplies', r'\b(towels?|toilet paper|soap|shampoos?|(?<!air )conditioners?|salt|pepper|(?:cooking|olive) oil|blankets?|pillows?|batter(?:y|ies)|remotes?|laundry|trash bags?|garbage bags?|dish pods?|dishwasher pods?|dishwasher tablets?|detergent)\b', 'Check onsite stock and the full conversation. If still needed, propose a suitable replacement with an all-in budget and verified arrival before checkout.', 'Possible small purchase', 'Normal'),
    ('Timing', r'\b(?:early check.?in|late check.?out|check in (?:early|around|at|before)|check out (?:late|around|at|after)|arriv\w* early)\b', 'Check the current turnover schedule and record the requested time in the property timezone before deciding. Update the reservation separately if approved.', 'Turnover review', 'Normal'),
    ('Instructions', r'\b(check.?out instructions|trash|garbage|wifi|wi-fi|parking|gaslog|fireplace)\b', 'Verify the property-specific instructions and prepare a clear answer for the manager to review. Add recurring questions to the guest guide.', 'Guest guide improvement', 'Normal'),
)
SIGNAL = re.compile(r'\b(no|not|missing|broken|stopped|dirty|cannot|can.t|don.t (?:have|see|find)|doesn.t|won.t|wasn.t|weren.t|isn.t|aren.t|neither|out of|please|need|bring|can we|could we|can you|could you|how|where|question|instructions|off the wall|permitted|request|could you resend|are there|which|is there|found|just checked in|stopped using|pooling|goes black|still|was full|dried food)\b', re.I)
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
RESOLUTION = re.compile(r'\b(?:found|located|fixed|resolved|sorted|got.{0,20}\bworking|(?:got|getting).{0,20}\bto work|(?:works?|working|connects?|connected) now|now (?:works?|working|connects?|connected)|all (?:good|set))\b', re.I)
NEGATED_RESOLUTION = re.compile(r'\b(?:not|never|still|cannot|can.t|couldn.t|isn.t|is not|hasn.t|haven.t|didn.t|doesn.t|don.t|won.t|without)\b', re.I)


def analyze(snapshot):
    issues = []
    current = dt.datetime.now(dt.timezone.utc)
    active = set(snapshot.get('active_reservation_ids', []))
    upcoming = set(snapshot.get('upcoming_reservation_ids', []))
    for conversation in snapshot['conversations']:
        messages = sorted(conversation['messages'], key=lambda message: message['created_at'])
        grouped = {}
        for index, message in enumerate(messages):
            text = message['body'].replace('\u2019', "'")
            if message['sender_type'] != 'guest':
                continue
            clauses = re.split(r'[.!?;\n]+|\bbut\b', text, flags=re.I)
            matched_categories = set()
            for category, pattern, remedy, route, priority in RULES + (
                ('Other', r'\b(?:add (?:an?|one|another) (?:overnight )?guest|extend (?:our|my) stay|pet (?:fee|policy)|where can we|how do (?:i|we))\b',
                 'Review the reservation and property policies before replying.', 'Manager review', 'Normal'),
            ):
                candidates = [clause for clause in clauses if re.search(pattern, clause, re.I) and (category == 'Safety' or SIGNAL.search(clause) or (category == 'Timing' and re.search(r'\bearly\b|\blate\b', clause, re.I)))]
                if category == 'Instructions' and 'Cleaning' in matched_categories:
                    candidates = [clause for clause in candidates if not re.search(r'\btrash was full\b', clause, re.I)]
                if category == 'Supplies':
                    candidates = [clause for clause in candidates if not re.search(r'\b((?:do not|don.t|no longer|never) need|not missing|have enough|no need)\b', clause, re.I) and re.search(r'\b(no|not|don.t (?:have|see|find)|missing|broken|weren.t|aren.t|out of|need|bring|can we|could we|can you|could you|please|is there|are there|only)\b', clause, re.I)]
                if category == 'Other' and matched_categories:
                    continue
                if not candidates:
                    continue
                matched_categories.add(category)
                issue = grouped.setdefault(category, {'id': conversation['reservation_id'] + ':' + category.lower(), 'category': category, 'priority': priority, 'route': route, 'recommendation': remedy, 'evidence': [], 'resolution_hint': None, 'hand_towel': False, 'item_types': [], 'topics': []})
                issue['evidence'].append(message)
                for topic, topic_pattern in TOPICS.items():
                    if topic not in issue['topics'] and any(re.search(topic_pattern, clause, re.I) for clause in candidates):
                        issue['topics'].append(topic)
                if category == 'Supplies':
                    for key, (_, item_pattern, _) in ITEMS.items():
                        if key not in issue['item_types'] and any(re.search(item_pattern, clause, re.I) for clause in candidates):
                            issue['item_types'].append(key)
                if category == 'Supplies' and any(re.search(r'\bhand towels?\b', clause, re.I) for clause in candidates):
                    issue['hand_towel'] = True
                    for clause in candidates:
                        quantity = re.search(r'\b(?:need|bring|send|provide|request|can we have|could we have|can we get|could we get)\s+(?:us\s+)?([1-9][0-9]?|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:(?:extra|additional|more)\s+)?hand towels?\b', clause, re.I)
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
                        clause = re.sub(r'^\s*never mind\b', '', clause, flags=re.I)
                        if '?' in clause or re.search(r'\b(?:if|when|would|could|should|will|please|hope|think|wonder|maybe)\b', clause, re.I):
                            continue
                        if issue['category'] != 'Supplies' and re.search(r'\b(?:found|located)\b', clause, re.I):
                            continue
                        if not RESOLUTION.search(clause) or NEGATED_RESOLUTION.search(clause):
                            continue
                        mentioned = {key for key, pattern in TOPICS.items() if re.search(pattern, clause, re.I)}
                        if 'wifi' in mentioned:
                            mentioned.discard('tv')
                        if 'toilet_paper' in mentioned:
                            mentioned.discard('toilet')
                        explicit = mentioned == {topic}
                        contextual = not mentioned and later is later_guests[0] and last['created_at'] == latest_complaint and re.search(r'\b(?:it|that|all good|all set)\b', clause, re.I)
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
            check_in = dt.datetime.fromisoformat(conversation['check_in'].replace('Z', '+00:00'))
            check_out = dt.datetime.fromisoformat(conversation['check_out'].replace('Z', '+00:00'))
            issue['lane'] = 'likely_resolved' if issue['resolution_hint'] else 'needs_attention'
            issue['upcoming'] = conversation['reservation_id'] in upcoming and current < check_in
            if issue['upcoming'] and issue['category'] == 'Timing':
                issue['lane'] = 'needs_attention'
            elif conversation['reservation_id'] not in active or not check_in <= current < check_out:
                issue['lane'] = 'practice'
            issues.append(issue)
    issues.sort(key=lambda issue: issue['latest_at'], reverse=True)
    return {'issues': issues, 'synced_at': snapshot['synced_at'], 'property_count': snapshot['property_count'], 'reservation_count': len(snapshot['conversations']), 'guest_message_count': sum(message['sender_type'] == 'guest' for conversation in snapshot['conversations'] for message in conversation['messages']), 'nontext_message_count': snapshot['nontext_message_count'], 'scope': snapshot['scope']}
