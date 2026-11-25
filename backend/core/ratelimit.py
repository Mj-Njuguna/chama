"""Fixed-window rate limits in Redis.

Counters live only in Redis, keyed by a keyed hash of the identifier (never a phone number or IP
in clear). A window is ``limit`` hits per ``window_seconds``; every hit counts, including
refused ones, so hammering does not reset anything.

If Redis is down, :func:`hit` raises :class:`RateLimitUnavailable`. Callers decide: OTP requests
fail closed (nothing is sent).
"""

import time
from dataclasses import dataclass
from functools import cache

import redis
from django.conf import settings

from core.keys import KeyRing, parse_key_ring
from core.redis import get_redis

_PREFIX = "rl:"


class RateLimitUnavailable(Exception):
    """The counter store could not be reached."""


@dataclass(frozen=True)
class Limit:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    retry_after: int  # seconds until the window ends; 0 when allowed


@cache
def _ring(raw: str) -> KeyRing:
    return parse_key_ring("AUDIT_HASH_KEYS", raw)


def _key(limit: Limit, identifier: str, window: int) -> str:
    digest = _ring(settings.AUDIT_HASH_KEYS).sign(f"ratelimit:{limit.name}", identifier.encode())
    return f"{_PREFIX}{limit.name}:{digest}:{window}"


def hit(limit: Limit, identifier: str) -> Verdict:
    now = time.time()
    window = int(now // limit.window_seconds)
    key = _key(limit, identifier, window)
    try:
        pipe = get_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, limit.window_seconds + 1)
        count = int(pipe.execute()[0])
    except redis.RedisError as exc:
        raise RateLimitUnavailable from exc
    if count <= limit.limit:
        return Verdict(True, 0)
    return Verdict(False, max(1, int((window + 1) * limit.window_seconds - now + 0.999)))
