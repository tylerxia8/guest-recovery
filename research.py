import re

RULES = (
    ('Safety', r'\b(smoke (?:coming|detected|filling)|smoke alarm (?:sounding|going off)|(?:smell|smelling) (?:of )?gas|gas leak|sparks|exposed wir\w*|carbon monoxide alarm|break.in)\b', 'Escalate to the property manager immediately. Confirm the situation and arrange an appropriate emergency or licensed response.', 'Manager intervention', 'Urgent'),
    ('Maintenance', r'\b(broken|not working|doesn.t work|won.t work|no hot water|leak\w*|off the wall|clogged)\b', 'Ask the manager to inspect the reported fault and arrange a repair. Confirm the exact item before considering a replacement.', 'Maintenance visit', 'High'),
    ('Cleaning', r'\b(dirty|unclean|stained|mold|mould|filthy|bedbugs?|cockroach\w*)\b', 'Review the photos or conversation, arrange a cleaner if needed, and add the issue to the turnover checklist.', 'Cleaning follow-up', 'Normal'),
    ('Supplies', r'\b(towels?|toilet paper|soap|shampoo|blankets?|pillows?|batteries|remote)\b', 'Check onsite stock and the full conversation. If still needed, propose a suitable replacement with an all-in budget and verified arrival before checkout.', 'Possible small purchase', 'Normal'),
    ('Instructions', r'\b(check.?out instructions|trash|garbage|wifi|wi-fi|parking|gaslog|fireplace)\b', 'Verify the property-specific instructions and prepare a clear answer for the manager to review. Add recurring questions to the guest guide.', 'Guest guide improvement', 'Normal'),
)
SIGNAL = re.compile(r'\b(no|not|missing|broken|dirty|cannot|can.t|weren.t|isn.t|aren.t|out of|please|need|bring|can we|could we|can you|could you|how|where|question|instructions|off the wall|permitted)\b', re.I)


def analyze(snapshot):
    issues = []
    for conversation in snapshot['conversations']:
        messages = sorted(conversation['messages'], key=lambda message: message['created_at'])
        grouped = {}
        for index, message in enumerate(messages):
            text = message['body']
            if message['sender_type'] != 'guest':
                continue
            clauses = re.split(r'[.!?;\n]+|\bbut\b', text, flags=re.I)
            for category, pattern, remedy, route, priority in RULES:
                candidates = [clause for clause in clauses if re.search(pattern, clause, re.I) and (category == 'Safety' or SIGNAL.search(clause))]
                if category == 'Supplies':
                    candidates = [clause for clause in candidates if not re.search(r'\b((?:do not|don.t|no longer|never) need|not missing|have enough|no need)\b', clause, re.I) and re.search(r'\b(no|not|missing|weren.t|aren.t|out of|need|bring|can we|could we|can you|could you|please)\b', clause, re.I)]
                if not candidates:
                    continue
                issue = grouped.setdefault(category, {'id': conversation['reservation_id'] + ':' + category.lower(), 'category': category, 'priority': priority, 'route': route, 'recommendation': remedy, 'evidence': [], 'resolution_hint': None, 'hand_towel': False})
                issue['evidence'].append(message)
                if category == 'Supplies' and any(re.search(r'\bhand towels?\b', clause, re.I) for clause in candidates):
                    issue['hand_towel'] = True
        for issue in grouped.values():
            if issue['category'] == 'Supplies' and all(re.search(r'\btowels?\b', evidence['body'], re.I) for evidence in issue['evidence']):
                last = issue['evidence'][-1]
                towel = 'hand towels?' if re.search(r'\bhand towels?\b', last['body'], re.I) else 'bath towels?' if re.search(r'\bbath towels?\b', last['body'], re.I) else 'towels?'
                for later in messages:
                    if later['created_at'] > last['created_at'] and later['sender_type'] == 'guest' and re.search(r'\b(found|located)\b.{0,35}\b' + towel + r'\b', later['body'], re.I) and not SIGNAL.search(later['body']):
                        issue['resolution_hint'] = later
            issue.update({'property': conversation['property'], 'reservation_id': conversation['reservation_id'], 'check_in': conversation['check_in'], 'check_out': conversation['check_out'], 'messages': messages, 'latest_at': issue['evidence'][-1]['created_at']})
            issue['status'] = 'Possible resolution' if issue['resolution_hint'] else 'Needs review'
            issues.append(issue)
    issues.sort(key=lambda issue: issue['latest_at'], reverse=True)
    return {'issues': issues, 'synced_at': snapshot['synced_at'], 'property_count': snapshot['property_count'], 'reservation_count': len(snapshot['conversations']), 'guest_message_count': sum(message['sender_type'] == 'guest' for conversation in snapshot['conversations'] for message in conversation['messages']), 'nontext_message_count': snapshot['nontext_message_count'], 'scope': snapshot['scope']}
