from __future__ import annotations

import sys

from celery import Celery
from celery.signals import worker_init

from app.config import get_settings, validate_startup
from app.db import validate_app_role

settings = get_settings()
validate_startup(settings)

celery_app = Celery("cleaning_coordination", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.beat_schedule = {}
celery_app.conf.task_default_queue = "foundation"
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.autodiscover_tasks(["app.workers"])


@worker_init.connect  # type: ignore[untyped-decorator]
def validate_database_role_on_worker_init(**_kwargs: object) -> None:
    """Validate database role before worker starts processing tasks.

    Uses worker_init signal which fires before pool, Beat (-B), and Consumer.
    Celery's Signal.send catches Exception but not SystemExit, so we catch
    any exception and raise SystemExit(1) to stop the worker. This includes
    ConfigError (privileged role) and OperationalError (bad password, DB down).

    Uses a throwaway NullPool engine to avoid polluting the cached engine
    before prefork. The cached engine will be created later in each child.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    try:
        engine = create_engine(
            settings.database_url,
            poolclass=NullPool,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000 -c lock_timeout=5000"},
        )
        validate_app_role(engine)
        engine.dispose()
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        print(f"ERROR: {code}: {e}", file=sys.stderr)
        raise SystemExit(1) from e


from app.workers import reservations as _reservation_workers

_ = _reservation_workers.run_reservation_sync
