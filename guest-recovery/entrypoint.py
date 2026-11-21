import os
import sys
from pathlib import Path

if os.geteuid() == 0:
    directory = Path('/data')
    if directory.is_symlink():
        raise SystemExit('State directory must not be a symbolic link')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(directory, 10001, 10001)
    os.chmod(directory, 0o700)
    database = Path(os.environ.get('DATABASE_PATH', '/data/recovery.sqlite3'))
    configuration = Path(os.environ.get('ENV_FILE', '/data/.env'))
    files = (database, Path(str(database) + '-wal'), Path(str(database) + '-shm'), configuration)
    for filename in files:
        if filename.parent != directory or filename.is_symlink():
            raise SystemExit('Application state must be a regular file inside /data')
        if filename.exists():
            if not filename.is_file():
                raise SystemExit('Application state must be a regular file')
            os.chown(filename, 10001, 10001)
            os.chmod(filename, 0o600)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parent / 'app.py')])
