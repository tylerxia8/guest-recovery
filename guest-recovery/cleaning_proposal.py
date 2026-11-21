import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def propose(issue, timezone, timestamp):
    timezone = timezone or 'America/New_York'
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        zone = None
    current = dt.datetime.fromisoformat(timestamp)
    evidence = issue['evidence']
    text = ' '.join(message['body'] for message in evidence).lower()
    tasks = []
    for pattern, task in (
        (r'\b(sheets?|linens?|bedding|mattress|blankets?)\b', 'Replace the affected linens and remake the bed'),
        (r'\b(towels?)\b', 'Replace the affected towels'),
        (r'\b(bathroom|toilet|tub|shower|bidet)\b', 'Reclean the affected bathroom areas'),
        (r'\b(floors?|sticky)\b', 'Clean the affected floors'),
        (r'\b(dishes|dishwasher|glass|glasses)\b', 'Clean the reported dishes or dishwasher'),
        (r'\b(kitchen|counter\w*|stove|oven|fridge|refrigerator)\b', 'Reclean the affected kitchen areas'),
        (r'\b(trash|garbage|rubbish)\b', 'Remove leftover trash and clean the bin'),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            tasks.append(task)
    kind = 'linens' if tasks and all('linens' in task or 'towels' in task for task in tasks) else 'turnover' if len(tasks) >= 4 else 'targeted'
    duration = 120 if kind == 'turnover' else min(90, max(30, 15 * len(tasks)))
    proposal = {'kind': kind, 'scope': '; '.join(tasks) or 'Reclean the areas reported by the guest', 'duration_minutes': duration, 'release_at': '', 'deadline_at': '', 'guest_access_confirmed': False, 'timezone': timezone, 'status': 'needs_details', 'question': '', 'evidence': []}
    if zone is None:
        proposal.update({'timezone': None, 'question': 'Please confirm the property’s local timezone and a suitable visit window before arranging entry.'})
        return proposal
    last_complaint = max(message['created_at'] for message in evidence)
    messages = sorted(issue['messages'], key=lambda message: message['created_at'])
    declined = re.compile(r"\b(no (?:cleaner|visit) needed|no need (?:to (?:come|clean|send)|for (?:a |the )?(?:cleaner|visit))|not needed|not necessary|don.t (?:need|want).{0,25}(?:cleaner|visit)|already cleaned|all cleaned up|all (?:fixed|resolved))\b", re.IGNORECASE)
    permission = re.compile(r'\b(?:you|the cleaner|your cleaner|they) (?:can|may|are welcome to) (?:come|enter|stop by|go in)|\b(?:please|yes[,]? please) (?:send|arrange|have).{0,25}\bcleaner\b|\bpermission to enter\b', re.IGNORECASE)
    refusal = re.compile(r"\b(?:do not|don.t|cannot|can.t|no|not|never)\b", re.IGNORECASE)
    cleaner = re.compile(r'\b(cleaner|cleaning|reclean|fresh (?:linens|sheets)|remake.{0,15}bed)\b', re.IGNORECASE)
    time_range = re.compile(r'\b(?:between|from)\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\s*(?:and|to|[-–])\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)', re.IGNORECASE)
    for index, message in enumerate(messages):
        if message['sender_type'] != 'guest' or message['created_at'] < last_complaint:
            continue
        body = message['body'].replace('\u2019', "'")
        if message['created_at'] > last_complaint and declined.search(body):
            proposal.update({'status': 'not_needed', 'question': '', 'guest_access_confirmed': False, 'release_at': '', 'deadline_at': '', 'evidence': [message['id']]})
            continue
        previous = messages[index - 1] if index else None
        accepted_offer = bool(previous and previous['sender_type'] != 'guest' and cleaner.search(previous['body']) and not refusal.search(previous['body']) and not re.search(r'\b(if|unless|maybe|perhaps|possibly)\b', previous['body'], re.IGNORECASE) and re.search(r'\b(?:can|may|could|shall|would)\b', previous['body'], re.IGNORECASE) and re.search(r'\b(?:come|enter|return|stop by|go in)\b', previous['body'], re.IGNORECASE) and re.fullmatch(r'(?:yes|sure|okay|ok|that works|sounds good|yes please)[,.!\s]*', body.strip(), re.IGNORECASE))
        qualified = '?' in body or bool(re.search(r'\b(if|unless|maybe|perhaps|possibly|or)\b', body, re.IGNORECASE))
        permits_entry = bool((permission.search(body) and not qualified) or accepted_offer)
        if proposal['status'] == 'not_needed':
            if not permits_entry:
                continue
            proposal.update({'status': 'needs_details', 'evidence': []})
        if refusal.search(body):
            proposal.update({'guest_access_confirmed': False, 'release_at': '', 'deadline_at': ''})
            continue
        if qualified:
            proposal['guest_access_confirmed'] = False
        if permits_entry:
            proposal['guest_access_confirmed'] = True
            proposal['evidence'].append(message['id'])
        timing = previous if accepted_offer else message
        timing_text = timing['body'].lower().replace('a.m.', 'am').replace('p.m.', 'pm')
        clauses = re.split(r'[.!?;\n]', timing_text)
        timing_clause = next((clause for clause in clauses if time_range.search(clause)), '')
        match = time_range.search(timing_clause)
        if match and qualified and not accepted_offer:
            proposal.update({'release_at': '', 'deadline_at': '', 'guest_access_confirmed': False})
            continue
        if not match:
            if re.search(r'\b(instead|reschedule|later|tomorrow|morning|afternoon|evening)\b', body, re.IGNORECASE):
                proposal.update({'release_at': '', 'deadline_at': '', 'guest_access_confirmed': False})
            continue
        proposal.update({'release_at': '', 'deadline_at': '', 'guest_access_confirmed': permits_entry})
        if re.search(r'\b(pacific|central|mountain|eastern|pst|pdt|cst|cdt|mst|mdt|est|edt|utc|gmt|my time|your time)\b', timing_clause):
            proposal['question'] = 'Please confirm the visit window in the property’s local time (' + timezone + ') and whether the cleaner may enter then.'
            continue
        local_date = dt.datetime.fromisoformat(timing['created_at']).astimezone(zone).date()
        date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b', timing_clause)
        if date_match:
            try:
                local_date = dt.date.fromisoformat(date_match[0])
            except ValueError:
                continue
        elif re.search(r'\btomorrow\b', timing_clause):
            local_date += dt.timedelta(days=1)
        elif not re.search(r'\btoday\b', timing_clause):
            continue
        clocks = []
        for value in match.groups():
            value = value.lower().replace('.', '').replace(' ', '')
            try:
                if value.endswith(('am', 'pm')):
                    clock = dt.datetime.strptime(value, '%I:%M%p' if ':' in value else '%I%p').time()
                elif ':' in value:
                    if not re.search(r'\b24.hour\b', timing_clause) and not any(int(part.split(':')[0]) > 12 or part.strip().startswith('00:') for part in match.groups()):
                        break
                    clock = dt.time.fromisoformat(value)
                else:
                    break
            except ValueError:
                break
            local = dt.datetime.combine(local_date, clock, zone)
            if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset() or local.astimezone(dt.UTC).astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                break
            clocks.append(local.astimezone(dt.UTC))
        if len(clocks) != 2:
            continue
        start, end = clocks
        if start < end and end - start >= dt.timedelta(minutes=duration):
            proposal.update({'release_at': start.isoformat(), 'deadline_at': end.isoformat()})
            proposal['evidence'].append(timing['id'])
    if proposal['status'] == 'not_needed':
        return proposal
    if proposal['release_at'] and not current < dt.datetime.fromisoformat(proposal['release_at']) < dt.datetime.fromisoformat(proposal['deadline_at']) <= current + dt.timedelta(hours=48):
        proposal.update({'release_at': '', 'deadline_at': ''})
    if proposal['question'] and not proposal['release_at']:
        return proposal
    if not proposal['release_at'] and not proposal['guest_access_confirmed']:
        proposal['question'] = "I'm sorry about the cleaning issue. May we arrange for a cleaner to return? What day and time window would work for you, and may they enter during that window?"
    elif not proposal['release_at']:
        proposal['question'] = 'What day and time window would work for the cleaner to return?'
    elif not proposal['guest_access_confirmed']:
        proposal['question'] = 'May the cleaner enter during the time window you mentioned?'
    else:
        proposal['status'] = 'ready'
    return proposal
