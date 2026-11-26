"""Read the pending work in a verified private Guest Messages workspace."""

import json
from contextlib import closing
from pathlib import Path
import os
import re
import sqlite3
import stat
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from uuid import UUID, NAMESPACE_URL, uuid5

from auth import AuthError
from cleaner_returns import NoRedirect


def items(directory, issuer, authorization):
    if re.fullmatch(r'Bearer opq_[A-Za-z0-9_-]{43}', authorization) is None:
        raise AuthError(401, 'Shared inbox access is required.')
    request = Request(issuer + '/auth/operations/inbox-identity', headers={'Authorization': authorization})
    try:
        with build_opener(NoRedirect()).open(request, timeout=6) as response:
            identity = json.loads(response.read(4096))
        workspace = str(UUID(identity['workspace_id']))
        owner = str(UUID(identity['workspace_owner_id']))
    except (URLError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise AuthError(403, 'Cleaner could not verify inbox access.') from None

    database = Path(directory) / (workspace + '.sqlite3')
    try:
        metadata = database.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise AuthError(403, 'The inbox workspace could not be verified.')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        marker = db.execute('SELECT workspace_id, account_id FROM lucena_workspace WHERE id=1').fetchone()
        if marker != (workspace, owner):
            raise AuthError(403, 'The inbox workspace could not be verified.')
        drafts = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_reply_drafts'").fetchone():
            outcome_filter = ""
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='private_reply_outcomes'").fetchone():
                outcome_filter = """
                    WHERE NOT EXISTS (
                        SELECT 1 FROM private_reply_outcomes o
                        WHERE o.property_id=d.property_id AND o.booking_id=d.booking_id
                            AND o.message_id=d.message_id AND o.status='accepted'
                    )
                """
            drafts = db.execute(f"""
                SELECT d.property_id, d.booking_id, u.name
                FROM private_reply_drafts d LEFT JOIN rental_units u ON u.id=d.property_id
                {outcome_filter}
                GROUP BY d.property_id, d.booking_id, u.name ORDER BY max(d.rowid) DESC LIMIT 100
            """).fetchall()
        orders = db.execute("""
            SELECT id, property FROM purchase_requests WHERE decision='pending'
            ORDER BY created_at DESC LIMIT 100
        """).fetchall()
    return [{
        'source': 'guest-replies',
        'id': str(uuid5(NAMESPACE_URL, f'lucena-draft:{workspace}:{property_id}:{booking_id}')),
        'title': 'Guest reply draft',
        'property': property_name,
        'next_action': 'Review the conversation and draft',
        'due_at': None,
        'urgent': False,
        'path': '/messages?' + urlencode({'property_external_id': property_id, 'reservation_external_id': booking_id}),
    } for property_id, booking_id, property_name in drafts] + [{
        'source': 'supplies',
        'id': str(uuid5(NAMESPACE_URL, f'lucena-supply:{workspace}:{identifier}')),
        'title': 'Supply order approval',
        'property': property_name,
        'next_action': 'Review the proposed order',
        'due_at': None,
        'urgent': False,
        'path': '/',
    } for identifier, property_name in orders]
