"""Call the scheduler's Python services in a single-workspace installation."""

import os
import sys
from functools import lru_cache
from pathlib import Path
from uuid import UUID

def workspace_id():
    value = os.environ.get('INTERNAL_SCHEDULER_WORKSPACE_ID')
    return str(UUID(value)) if value else ''


def origin(workspace):
    return 'internal-scheduler:' + workspace


@lru_cache(maxsize=1)
def _service(workspace):
    directory = Path(os.environ.get('INTERNAL_SCHEDULER_BACKEND',
                                    Path(__file__).resolve().parents[1] / 'cleaner-scheduling' / 'backend'))
    if not (directory / 'app' / 'db.py').is_file():
        raise ValueError('The internal scheduler backend is missing.')
    sys.path.insert(0, str(directory.resolve()))
    from app.config import get_settings
    from app.db import get_engine, session_factory, validate_app_role
    from app.services import guest_recovery

    settings = get_settings()
    validate_app_role(get_engine(settings.database_url))
    return session_factory(settings, UUID(workspace)), guest_recovery


def request(workspace, method, path, body):
    from cleaner_returns import SchedulerNotFound
    try:
        sessions, service = _service(workspace)
        with sessions.begin() as session:
            result = service.handle(session, method, path, body)
    except LookupError:
        raise SchedulerNotFound('This request has not been found in the scheduler') from None
    except ValueError:
        raise
    except Exception:
        raise ValueError('The internal scheduler could not verify this request. Check its database and workspace setup.') from None
    return result
