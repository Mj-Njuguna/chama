"""The client's IP address, trusting exactly ``TRUSTED_PROXY_COUNT`` proxies.

With 0 proxies, ``REMOTE_ADDR`` is the client. With N, the client is the N-th entry from the
right of ``X-Forwarded-For``: each trusted proxy appends the address it saw, and anything to the
left of that can be forged by the client. Production must set the count explicitly; a wrong
value lets a client pick its own IP or puts every user behind one IP.
"""

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def _valid(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: HttpRequest) -> str | None:
    remote = _valid(request.META.get("REMOTE_ADDR", ""))
    proxies = int(settings.TRUSTED_PROXY_COUNT)
    if proxies <= 0:
        return remote
    forwarded = [part for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part]
    if len(forwarded) < proxies:
        return remote
    return _valid(forwarded[-proxies]) or remote
