import datetime as dt
import os
import re
import uuid
from zoneinfo import ZoneInfo

POLICY_VERSION = '2026-11-17-v2'
EASTERN = ZoneInfo('America/New_York')
CLOCK = r'(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)(?=\s|[?!.,;]|$)'
EARLY = re.compile(r'\b(?:earl(?:y|ier|iest) check[ -]?in|check[ -]?in (?:(?:a (?:bit|little)|an? (?:extra )?hour|\d+ hours?) )?(?:early|earlier|before)|earl(?:y|ier) (?:arrival|entry)|(?:arriv(?:e|ing)|come|get (?:in|into (?:the )?(?:house|home|property|unit))) (?:(?:a (?:bit|little)|an? (?:extra )?hour|\d+ hours?) )?(?:early|earlier|before))\b', re.IGNORECASE)
LATE = re.compile(r'\b(?:late check[ -]?out|later check[ -]?out|late(?:r)? departure|latest.{0,20}check[ -]?out|check[ -]?out (?:(?:a (?:bit|little)|an? (?:extra )?hour|\d+ hours?) )?(?:late|later|after)|extend (?:the |our |my )?check[ -]?out|(?:leave|depart) (?:a (?:bit|little) )?(?:late|later))\b', re.IGNORECASE)
ARRIVAL = re.compile(r'\b(?:check[ -]?in|arriv(?:e|al|ing)|get in(?:to (?:the )?(?:house|home|property|unit))?|enter (?:the )?(?:house|home|property|unit))\b', re.IGNORECASE)
DEPARTURE = re.compile(r'\b(?:check[ -]?out|depart(?:ure|ing)?|leave)\b', re.IGNORECASE)


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Stay policy timestamps must include a timezone')
    return parsed.astimezone(EASTERN)


def superseding_message(issue, message, kind, evaluated_at):
    original = request(issue, message, evaluated_at)
    for later in sorted(issue['messages'], key=lambda item: timestamp(item['created_at'])):
        if later['sender_type'] != 'guest' or not timestamp(message['created_at']) < timestamp(later['created_at']) <= timestamp(evaluated_at):
            continue
        if re.fullmatch(r'\s*(?:never\s?mind|cancel (?:that|the request))(?:,?\s*(?:please|thanks|thank you))?[.!]*\s*', later['body'], re.IGNORECASE) or re.search(r'\bnormal (?:check[ -]?out|check[ -]?in) is fine\b|\b(?:never\s?mind|cancel|withdraw|no longer (?:need|want)|no need (?:for|to)|(?:don.t|do not|won.t) need)\b[^.!?;\n]{0,50}\b(?:early|late|check[ -]?in|check[ -]?out|arrival|departure)\b', later['body'], re.IGNORECASE):
            return later['id']
        replacement = request(issue, later, evaluated_at)
        if replacement and replacement['kind'] == kind and replacement['reason'] != 'policy_information' and original and replacement['target_at'] != original['target_at']:
            return later['id']
    return None


def local_time(day, clock):
    value = dt.datetime.combine(day, clock, EASTERN)
    if value.replace(fold=0).utcoffset() != value.replace(fold=1).utcoffset() or value.astimezone(dt.UTC).astimezone(EASTERN).replace(tzinfo=None) != value.replace(tzinfo=None):
        return None
    return value


def requested_clock(text):
    matches = list(re.finditer(r'\b(?:at|by|around|until|before|after)\s+(' + CLOCK + ')', text, re.IGNORECASE))
    if not matches:
        matches = list(re.finditer(r'\b(?:check[ -]?(?:in|out)|arriv(?:e|al)|leave)\s+(' + CLOCK + ')', text, re.IGNORECASE))
    if not matches:
        matches = list(re.finditer(r'\b(' + CLOCK + r')\s+(?:check[ -]?(?:in|out)|arrival|departure)\b', text, re.IGNORECASE))
    if len(matches) != 1:
        return None, 'ambiguous' if matches else ''
    match = matches[0]
    if re.search(r'\b(?:before|after)\s+', match[0], re.IGNORECASE):
        return None, 'range'
    value = re.sub(r'[.\s]', '', match[1].casefold())
    if value == 'noon':
        return dt.time(12), 'explicit'
    if value == 'midnight':
        return dt.time(0), 'explicit'
    if value.endswith(('am', 'pm')):
        try:
            return dt.datetime.strptime(value, '%I:%M%p' if ':' in value else '%I%p').time(), 'explicit'
        except ValueError:
            return None, 'ambiguous'
    if value in {'11', '11:00', '12', '12:00'}:
        return dt.time(int(value.split(':')[0])), 'explicit'
    return None, 'ambiguous'


def request(issue, message, evaluated_at):
    schedule_based = bool(os.environ.get('INTERNAL_SCHEDULER_WORKSPACE_ID'))
    body = message['body']
    if not isinstance(body, str):
        raise TypeError('Guest request must be text')
    text = body.casefold().replace('\u2019', "'")
    if re.search(r"\b(?:don.t|do not|won.t|will not|no longer|not|never)\s+(?:need|want|request(?:ing)?|asking for)\s+(?:an?\s+|to\s+)?(?:check[ -]?(?:in|out)\s+)?(?:early|late)\b|\bno need for\s+(?:an?\s+)?(?:early|late)\b|\b(?:we|i) (?:have |already |just )?(?:checked out|departed)\b", text):
        return None
    arrival = timestamp(issue['check_in'])
    departure = timestamp(issue['check_out'])
    received = timestamp(message['created_at'])
    current = timestamp(evaluated_at)
    if arrival >= departure:
        raise ValueError('Reservation interval is invalid')
    context = re.split(r'\b(?:can|could|may|would)\s+(?:we|i)\b', text)[-1]
    clock, clock_source = requested_clock(context)
    relative = re.search(r'\b(?:an?|one|two|three|\d+)\s+(?:extra\s+)?hours?\s+(earlier|later|early|late)\b', context)
    if relative and not clock:
        clock_source = 'ambiguous'
        amount = relative[0].split()[0]
        hours = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3}.get(amount)
        hours = int(amount) if hours is None and amount.isdigit() else hours
        if hours and hours <= 12:
            baseline = arrival if relative[1] in ('earlier', 'early') else departure
            adjusted = baseline + dt.timedelta(hours=-hours if relative[1] in ('earlier', 'early') else hours)
            if adjusted.date() == baseline.date():
                clock, clock_source = adjusted.time().replace(tzinfo=None), 'explicit'
    arrival_clock = local_time(arrival.date(), clock) if clock else None
    departure_clock = local_time(departure.date(), clock) if clock else None
    wants_early = bool(EARLY.search(text) or ARRIVAL.search(context) and (arrival_clock and arrival_clock < arrival or clock_source == 'ambiguous'))
    wants_late = bool(LATE.search(text) or DEPARTURE.search(context) and (departure_clock and departure_clock > departure or clock_source == 'ambiguous'))
    if not wants_early and not wants_late:
        return None
    kind = 'early_checkin' if wants_early else 'late_checkout'
    event_date = arrival.date() if wants_early else departure.date()
    cutoff = dt.datetime.combine(departure.date() - dt.timedelta(days=1), dt.time(20), EASTERN)
    target = None
    source = clock_source or 'default'
    reason = 'cleaning_readiness_required' if wants_early else 'schedule_confirmation_required'
    decision = 'pending'
    ambiguous = wants_early and wants_late
    if ambiguous:
        reason = 'multiple_stay_changes'
    current_checkout = max(departure, dt.datetime.combine(departure.date(), dt.time(10), EASTERN))
    current_checkin = arrival
    if issue.get('effective_checkout_at') or issue.get('effective_check_out'):
        current_checkout = max(current_checkout, timestamp(issue.get('effective_checkout_at') or issue['effective_check_out']))
    if issue.get('effective_checkin_at') or issue.get('effective_check_in'):
        current_checkin = min(current_checkin, timestamp(issue.get('effective_checkin_at') or issue['effective_check_in']))
    if clock and (arrival_clock if wants_early else departure_clock) is None:
        ambiguous = True
        reason = 'time_clarification_required'
    abbreviation = re.search(r'\b(?:est|edt)\b', context)
    if abbreviation and abbreviation[0].upper() != dt.datetime.combine(event_date, dt.time(12), EASTERN).tzname():
        ambiguous = True
        reason = 'timezone_clarification_required'
    if re.search(r'\b(?:pst|pdt|cst|cdt|mst|mdt|utc|gmt|pacific|central|mountain|my time|your time)\b', context):
        ambiguous = True
        reason = 'timezone_clarification_required'
    for word, date in (('today', received.date()), ('tomorrow', received.date() + dt.timedelta(days=1))):
        if re.search(r'\b' + word + r'\b', context) and date != event_date:
            ambiguous = True
            reason = 'date_clarification_required'
    days = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
    if any(re.search(r'\b' + day + r'\b', context) and index != event_date.weekday() for index, day in enumerate(days)):
        ambiguous = True
        reason = 'date_clarification_required'
    explicit_dates = []
    for value in re.findall(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b', context):
        try:
            if '/' in value:
                pieces = [int(part) for part in value.split('/')]
                year = pieces[2] if len(pieces) == 3 else event_date.year
                explicit_dates.append(dt.date(year + 2000 if year < 100 else year, pieces[0], pieces[1]))
            else:
                explicit_dates.append(dt.date.fromisoformat(value))
        except ValueError:
            ambiguous = True
            reason = 'date_clarification_required'
    months = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december')
    for match in re.finditer(r'\b(' + '|'.join(months) + r')\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b', context):
        try:
            explicit_dates.append(dt.date(int(match[3]) if match[3] else event_date.year, months.index(match[1]) + 1, int(match[2])))
        except ValueError:
            ambiguous = True
            reason = 'date_clarification_required'
    if any(value != event_date for value in explicit_dates):
        ambiguous = True
        reason = 'date_clarification_required'
    if re.search(r'\b(?:between|either|or)\b', context) and (clock or re.search(r'\d', context)):
        ambiguous = True
        reason = 'time_clarification_required'
    if clock_source == 'ambiguous' or not clock and re.search(r'\b(?:at|by|around|until)\s+\d', context):
        ambiguous = True
        reason = 'time_clarification_required'
    if wants_early:
        if not ambiguous and clock and arrival_clock < arrival:
            target = arrival_clock
        elif not ambiguous and not clock and current.date() == arrival.date() and current < arrival:
            target = current
            source = 'ready_now'
        elif not ambiguous:
            reason = 'arrival_time_required'
        if arrival_clock and arrival_clock >= arrival:
            reason = 'not_an_early_arrival'
    else:
        if schedule_based and not ambiguous and clock_source != 'range':
            target = departure_clock if clock is not None else departure + dt.timedelta(hours=1)
            if target is None or target.date() != departure.date():
                target = None
                reason = 'time_clarification_required'
            elif target <= current_checkout:
                target = None
                reason = 'already_covered'
        elif received >= cutoff and not schedule_based:
            decision, reason = 'denied', 'after_cutoff'
        elif not ambiguous and clock_source == 'range':
            reason = 'time_clarification_required'
        elif not ambiguous and (clock is None or clock in (dt.time(11), dt.time(12))):
            target = dt.datetime.combine(departure.date(), clock or dt.time(11), EASTERN)
            if target <= current_checkout:
                target = None
                reason = 'already_covered'
        elif not ambiguous:
            reason = 'unsupported_checkout_time'
    if received > current:
        target = None
        reason = 'request_timestamp_in_future'
    if re.search(r'\b(?:policy|policies|rules)\b', context) and not re.search(r"\b(?:can|could|may|would)\s+(?:we|i)\b|\b(?:we|i).{0,10}(?:need|want|request|hoping)\b", text):
        target = None
        decision, reason = 'pending', 'policy_information'
    normalized_clock = clock.isoformat() if clock else 'ready_now' if wants_early and clock_source in ('', 'range') else 'default_plus_hour' if schedule_based and wants_late and not clock_source else 'default_11' if wants_late and not clock_source else 'unresolved:' + context
    normalized_target = event_date.isoformat() + ':' + normalized_clock
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, 'guest-recovery:stay-policy:' + issue['reservation_id'] + ':' + message['id'] + ':' + kind + ':' + normalized_target))
    result = {'request_id': identifier, 'policy_version': POLICY_VERSION, 'kind': kind, 'requested_at': received.astimezone(dt.UTC).isoformat(), 'target_at': target.astimezone(dt.UTC).isoformat() if target else None, 'target_source': source, 'cutoff_at': cutoff.astimezone(dt.UTC).isoformat(), 'decision': decision, 'reason': reason, 'can_request_scheduler': bool(target and decision == 'pending' and received <= current and (target > current or wants_early and target.date() == current.date() and current < arrival)), 'evaluated_at': current.astimezone(dt.UTC).isoformat(), 'booked_checkin_at': arrival.astimezone(dt.UTC).isoformat(), 'current_checkout_at': current_checkout.astimezone(dt.UTC).isoformat(), 'current_checkin_at': current_checkin.astimezone(dt.UTC).isoformat()}
    result['schedule_based'] = schedule_based
    if source == 'ready_now' and target and decision == 'pending' and received <= current:
        result['can_request_scheduler'] = True
    result['body'] = policy_reply(result)
    return result


def policy_reply(proposal, remote=None):
    kind = proposal['kind']
    current_checkout = timestamp(proposal['current_checkout_at']).strftime('%-I:%M %p') + ' Eastern Time'
    current_checkin = timestamp(proposal['current_checkin_at']).strftime('%-I:%M %p') + ' Eastern Time'
    if proposal['reason'] == 'guest_request_changed':
        return 'We’ve noted your updated request. We’ll review the latest details before confirming a change to your stay.'
    if proposal['reason'] == 'policy_information':
        if kind == 'early_checkin':
            return 'Early check-in is available once the home has been cleaned and is ready, without affecting another guest’s stay. Please wait for readiness confirmation before entering.'
        if proposal.get('schedule_based'):
            return 'A checkout extension of up to one hour is available before the checkout-day cleaning schedule is released, if the turnover and the next guest’s stay remain protected. A longer extension needs confirmation that cleaning and the next stay still fit.'
        return 'Requests received before 8 p.m. Eastern Time the night before checkout are eligible for 11 a.m. checkout when cleaning and the next guest’s stay remain protected. Noon checkout requires cleaning-schedule approval. Requests at or after that cutoff cannot receive an extension.'
    if remote and remote.get('request_id') != proposal['request_id']:
        raise ValueError('Scheduler response belongs to a different guest request')
    if remote and remote.get('decision') == 'approved':
        if proposal['decision'] == 'denied' or not proposal['target_at']:
            raise ValueError('Scheduler approval does not match the policy request')
        if kind == 'early_checkin':
            ready = timestamp(remote.get('target_at') or remote['effective_checkin_at'])
            if ready > timestamp(proposal.get('booked_checkin_at', proposal['current_checkin_at'])):
                raise ValueError('Scheduler early arrival is outside the requested window')
            ready = max(ready, timestamp(proposal['target_at']))
            if ready <= timestamp(proposal['evaluated_at']):
                return 'The home has been cleaned and is ready. You may check in now.'
            return 'The home has been cleaned, and your early check-in is confirmed for ' + ready.strftime('%-I:%M %p') + ' Eastern Time.'
        checkout = timestamp(remote.get('target_at') or proposal['target_at'])
        if checkout < timestamp(proposal['target_at']):
            raise ValueError('Scheduler checkout differs from the requested time')
        checkout = max(checkout, timestamp(proposal['current_checkout_at']))
        return 'Your late checkout is confirmed for ' + checkout.strftime('%-I:%M %p') + ' Eastern Time.'
    if proposal['reason'] == 'after_cutoff':
        return 'Late checkout requests must be received before 8 p.m. Eastern Time the night before departure. Since this request arrived at or after that cutoff, we cannot offer this checkout extension. Your current checkout time remains ' + current_checkout + '.'
    if proposal['reason'] == 'multiple_stay_changes':
        if proposal.get('schedule_based'):
            return 'Please confirm each requested time in Eastern Time. Early arrival depends on cleaning completion. We’ll check a later checkout against the cleaning schedule and the next stay.'
        return 'Early check-in requires the home to be cleaned and ready. Late checkout requests must arrive before 8 p.m. Eastern Time the night before departure, and noon checkout requires cleaning-schedule confirmation. Please confirm the date and time for each change you would like. Neither change can affect another guest’s stay.'
    if proposal['reason'] == 'already_covered':
        return 'Your current checkout time is ' + current_checkout + '. The time you requested is already within your current stay.'
    if remote and remote.get('decision') == 'denied':
        return 'We cannot accommodate this earlier arrival because the home is not ready or it would affect another guest’s stay. Your current check-in time remains ' + current_checkin + '.' if kind == 'early_checkin' else 'We cannot accommodate this checkout extension without affecting cleaning or another guest’s stay. Your current checkout time remains ' + current_checkout + '.'
    if kind == 'early_checkin':
        if proposal['reason'] in {'arrival_time_required', 'time_clarification_required', 'timezone_clarification_required', 'date_clarification_required'}:
            return 'Early check-in depends on the home being cleaned and ready, without affecting another guest’s stay. Please confirm the arrival date and time you would like, in Eastern Time. Please wait for confirmation before entering.'
        return 'Early check-in is possible once the home has been cleaned and is ready, as long as it does not affect another guest’s stay. We need to confirm that it is ready before you enter. Please wait for confirmation.'
    if proposal.get('schedule_based'):
        if not proposal['target_at']:
            return 'Please confirm the checkout time you’d like, in Eastern Time. We’ll check it against the cleaning schedule and the next guest’s stay.'
        return 'We’re checking whether your requested checkout time fits the cleaning schedule and the next guest’s stay. Please keep your current checkout time until we confirm.'
    if proposal['reason'] in {'time_clarification_required', 'timezone_clarification_required', 'date_clarification_required', 'unsupported_checkout_time', 'not_a_late_departure'}:
        return 'Our standard checkout is 10 a.m. Requests received before 8 p.m. Eastern Time the night before departure may qualify for 11 a.m.; noon checkout also requires confirmation that the cleaning schedule can accommodate it. Please confirm the checkout time you are requesting. Any change must protect the next guest’s stay.'
    target = timestamp(proposal['target_at']) if proposal['target_at'] else None
    clock = 'noon' if target and target.hour == 12 else '11 a.m.'
    return 'Your request was received before the 8 p.m. Eastern Time cutoff the night before departure. We need to confirm that ' + clock + ' checkout fits the cleaning schedule without affecting the next guest’s stay before confirming the change.'
