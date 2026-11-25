"""The shared Redis client. Redis is a cache and counter store only; callers must
decide what happens when it is down (: OTP requests fail closed)."""

from functools import cache

import redis
from django.conf import settings

# Short timeouts: a slow Redis must not hold a request for long.
SOCKET_TIMEOUT_SECONDS = 1.0


@cache
def _client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
    )


def get_redis() -> redis.Redis:
    return _client(settings.REDIS_URL)
