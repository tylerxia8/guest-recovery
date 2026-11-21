from __future__ import annotations

from typing import cast

import redis
from redis.exceptions import RedisError

from app.clock import Clock
from app.domain.errors import AppError


def enforce_rate_limit(
    client: redis.Redis,
    *,
    clock: Clock,
    subject: str,
    bucket: str,
    limit: int,
) -> None:
    window = clock.now().strftime("%Y%m%d%H%M")
    key = f"rl:{bucket}:{subject}:{window}"
    try:
        count = int(
            cast(
                int,
                client.eval(
                    "local count = redis.call('INCR', KEYS[1]); "
                    "if count == 1 then redis.call('EXPIRE', KEYS[1], 120) end; return count",
                    1,
                    key,
                ),
            )
        )
    except RedisError as exc:
        raise AppError(
            "dependency_unavailable",
            "Rate limit storage is unavailable.",
            503,
        ) from exc
    if count > limit:
        raise AppError(
            "rate_limited",
            "Rate limit exceeded.",
            429,
            {"limit_per_minute": limit},
        )
