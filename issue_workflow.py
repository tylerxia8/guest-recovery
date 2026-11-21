"""Manager decisions for issues inferred from a Hospitable snapshot."""

import hashlib
import json

ACTIONS = {
    'Timing': ('approved', 'declined'),
    'Maintenance': ('handoff_recorded', 'resolved'),
    'Cleaning': ('handoff_recorded', 'resolved'),
    'Safety': ('acknowledged', 'resolved'),
    'Supplies': ('resolved',),
    'Instructions': ('resolved',),
    'Other': ('resolved',),
}


def fingerprint(issue):
    evidence = [issue['evidence'], issue['lane'], issue['check_in'], issue['check_out']]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def attach(db, issue):
    row = db.execute('SELECT * FROM issue_workflow WHERE issue_id = ?', (issue['id'],)).fetchone()
    issue['workflow'] = {
        'version': row['version'] if row else 0,
        'decision': row['decision'] if row else None,
        'note': row['note'] if row else '',
        'fingerprint': fingerprint(issue),
        'stale': bool(row and row['fingerprint'] != fingerprint(issue)),
    }


def save(db, data, timestamp):
    from app import audit
    from research import analyze

    snapshot = db.execute('SELECT body FROM research_snapshot WHERE id = 1').fetchone()
    issue = next((issue for issue in analyze(json.loads(snapshot['body']))['issues']
                  if issue['id'] == data.get('issue_id')), None) if snapshot else None
    if not issue:
        raise ValueError('Issue is no longer available. Refresh the inbox.')
    attach(db, issue)
    previous = issue['workflow']
    if (data.get('fingerprint') != previous['fingerprint']
            or type(data.get('version')) is not int or data['version'] != previous['version']):
        raise ValueError('The issue changed. Refresh the inbox before recording a decision.')
    decision = data.get('decision')
    note = data.get('note')
    if decision not in ACTIONS[issue['category']] or not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
        raise ValueError('Choose an available action and enter a note of up to 1000 characters.')
    if issue['category'] == 'Safety' and decision == 'resolved' and (
            previous['decision'] != 'acknowledged' or previous['stale']):
        raise ValueError('Acknowledge the current safety report before marking it resolved.')
    version = previous['version'] + 1
    db.execute(
        """INSERT INTO issue_workflow (issue_id, decision, note, fingerprint, version)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(issue_id) DO UPDATE SET decision=excluded.decision, note=excluded.note,
               fingerprint=excluded.fingerprint, version=excluded.version""",
        (issue['id'], decision, note.strip(), previous['fingerprint'], version),
    )
    audit(db, issue['id'], 'issue_decision_recorded', {'decision': decision, 'version': version})
    return {'version': version}
