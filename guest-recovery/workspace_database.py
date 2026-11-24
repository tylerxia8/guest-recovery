"""Provision a private Guest Messages database for a verified Lucena identity."""

import os
import secrets
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from auth import timestamp

PROVISION_LOCK = threading.Lock()


def provision(directory, workspace_id, account_id, initialize, *, owner_id=None):
    """Create or resolve a workspace database without copying installation data.

    The caller must supply identities obtained from Cleaner's verified exchange.
    """
    workspace = str(UUID(workspace_id))
    account = str(UUID(account_id))
    owner = str(UUID(owner_id)) if owner_id is not None else account
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError('Guest Messages workspace directory must be private.')
    database = root / (workspace + '.sqlite3')
    with PROVISION_LOCK:
        if not database.exists() and not database.is_symlink():
            # Publish only a fully initialized database. Linking also refuses
            # to replace a database provisioned by a concurrent process.
            with TemporaryDirectory(dir=root) as staging:
                candidate = Path(staging) / 'workspace.sqlite3'
                candidate.touch(mode=0o600)
                initialize(candidate)
                with closing(sqlite3.connect(candidate, timeout=5)) as db, db:
                    db.execute('CREATE TABLE lucena_workspace (id INTEGER PRIMARY KEY CHECK (id = 1), workspace_id TEXT NOT NULL, account_id TEXT NOT NULL)')
                    db.execute('INSERT INTO lucena_workspace VALUES (1, ?, ?)', (workspace, owner))
                try:
                    os.link(candidate, database)
                except FileExistsError:
                    pass
        current = database.lstat()
        if not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid() or current.st_mode & 0o077:
            raise ValueError('Guest Messages workspace database must be private.')
        with closing(sqlite3.connect(database, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            marker = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'lucena_workspace'").fetchone()
            if not marker:
                raise ValueError('Guest Messages workspace database is already in use.')
            identity = db.execute('SELECT workspace_id, account_id FROM lucena_workspace WHERE id = 1').fetchone()
            if not identity or tuple(identity) != (workspace, owner):
                raise ValueError('Guest Messages workspace belongs to a different account.')
            db.execute(
                'INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?)',
                (account, 'Lucena manager ' + account[:8], secrets.token_bytes(16), secrets.token_bytes(32),
                 'manager', timestamp()),
            )
            user = db.execute('SELECT * FROM users WHERE id = ?', (account,)).fetchone()
            if user is None:
                raise ValueError('Guest Messages workspace account is unavailable.')
    return database, dict(user)
