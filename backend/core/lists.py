"""The list standard: every list endpoint pages through this module.

- **Opaque, signed cursors.** A cursor is ``django.core.signing`` data (own salt, timestamped,
  rotated with ``SECRET_KEY``/``SECRET_KEY_FALLBACKS``) holding the endpoint name, a
  fingerprint of the filters, search and sort, and the sort key of the last row returned. A
  cursor that was changed, belongs to another endpoint, is replayed with other parameters, has
  the wrong shape or is older than ``LIST_CURSOR_MAX_AGE_SECONDS`` is ``400 validation_error``
  with ``field_errors.cursor = ["invalid"]``. A cursor only says where to resume: each page is
  read again under RLS, so a forged one could at most move the
  starting point within rows the caller may see.
- **Stable total order.** Every sort ends with the primary key, all in one direction, and pages
  continue with a keyset condition (``(a, b, id) > (last a, last b, last id)``), never OFFSET.
  Rows inserted while paging cause no duplicates or gaps among rows that already existed.
  Sort fields must be NOT NULL.
- **Allowlists.** Filters, sorts and search are declared per endpoint in a :class:`ListSpec`.
  Any other query parameter is refused (400), not ignored. Each allowed field is backed by an
  index; the PR adding an endpoint lists them.
- **Page size.** ``limit`` defaults to ``LIST_DEFAULT_PAGE_SIZE``; above
  ``LIST_MAX_PAGE_SIZE`` it is capped, not refused.
- **Scope first.** The caller passes a queryset that is already narrowed by RLS and
  the owning module; this module only filters, orders and slices it.
- No total counts (totals come from summary endpoints).
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.core import signing
from django.db.models import F, Model, Q, QuerySet
from rest_framework.exceptions import ValidationError

CURSOR_SALT = "core.lists.cursor.v1"
SEARCH_MAX_LENGTH = 50
_RESERVED = frozenset({"limit", "cursor", "sort", "search"})


@dataclass(frozen=True)
class Filter:
    """One allowed filter: ``parse`` turns the raw string into a value (raise ValueError to
    refuse it), ``apply`` narrows the queryset with it."""

    parse: Callable[[str], Any]
    apply: Callable[[QuerySet[Any], Any], QuerySet[Any]]


@dataclass(frozen=True)
class ListSpec:
    """What one list endpoint allows. ``sorts`` maps a public sort name to the model fields it
    orders by (ascending; ``-name`` reverses all of them); the primary key is always appended."""

    endpoint: str
    sorts: Mapping[str, tuple[str, ...]]
    default_sort: str
    filters: Mapping[str, Filter] = field(default_factory=dict)
    search: Callable[[QuerySet[Any], str], QuerySet[Any]] | None = None

    def __post_init__(self) -> None:
        if self.default_sort.removeprefix("-") not in self.sorts:
            raise ValueError(f"{self.endpoint}: default sort is not in the allowlist")
        clash = _RESERVED & set(self.filters)
        if clash:
            raise ValueError(f"{self.endpoint}: filter names clash with {sorted(clash)}")


@dataclass(frozen=True)
class Page:
    rows: list[Any]
    next_cursor: str | None


def invalid(param: str, code: str = "invalid") -> ValidationError:
    return ValidationError({param: [code]})


def _fingerprint(spec: ListSpec, params: Mapping[str, str]) -> str:
    kept = sorted((k, v) for k, v in params.items() if k not in ("cursor", "limit"))
    raw = json.dumps([spec.endpoint, kept], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _plain(value: object) -> object:
    if value is None:
        raise RuntimeError("list sort fields must be NOT NULL")
    if isinstance(value, (uuid.UUID, date, datetime)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, (str, int)):
        return value
    raise RuntimeError(f"unsupported sort value type {type(value).__name__}")


def _limit(raw: str | None) -> int:
    if raw is None:
        return int(settings.LIST_DEFAULT_PAGE_SIZE)
    try:
        limit = int(raw)
    except ValueError:
        raise invalid("limit") from None
    if limit < 1:
        raise invalid("limit")
    return min(limit, int(settings.LIST_MAX_PAGE_SIZE))


def _params(query: Mapping[str, Any]) -> dict[str, str]:
    """One value per parameter; a repeated parameter is refused."""
    getlist = getattr(query, "getlist", None)
    out: dict[str, str] = {}
    for key in query:
        values = getlist(key) if getlist else [query[key]]
        if len(values) != 1:
            raise invalid(key, "repeated")
        out[key] = str(values[0])
    return out


def paginate[M: Model](query: Mapping[str, Any], queryset: QuerySet[M], spec: ListSpec) -> Page:
    """One page of ``queryset`` (already scoped) per ``spec`` and the request's query string."""
    params = _params(query)
    allowed = {"limit", "cursor", "sort", *spec.filters}
    if spec.search is not None:
        allowed.add("search")
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValidationError({name: ["not_allowed"] for name in unknown})

    for name, spec_filter in spec.filters.items():
        if name in params:
            try:
                value = spec_filter.parse(params[name])
            except (ValueError, TypeError):
                raise invalid(name) from None
            queryset = spec_filter.apply(queryset, value)

    if "search" in params and spec.search is not None:
        term = params["search"].strip()
        if not 1 <= len(term) <= SEARCH_MAX_LENGTH:
            raise invalid("search")
        queryset = spec.search(queryset, term)

    sort = params.get("sort", spec.default_sort)
    descending = sort.startswith("-")
    if sort.removeprefix("-") not in spec.sorts:
        raise invalid("sort", "not_allowed")
    key_fields = (*spec.sorts[sort.removeprefix("-")], "pk")
    keys = [f"_list_k{i}" for i in range(len(key_fields))]
    queryset = queryset.annotate(**{k: F(f) for k, f in zip(keys, key_fields, strict=True)})
    queryset = queryset.order_by(*(f"-{k}" if descending else k for k in keys))

    fingerprint = _fingerprint(spec, params)
    if "cursor" in params:
        after = _read_cursor(params["cursor"], spec, fingerprint, len(keys))
        queryset = queryset.filter(_after(keys, after, descending))

    limit = _limit(params.get("limit"))
    rows = list(queryset[: limit + 1])
    if len(rows) <= limit:
        return Page(rows, None)
    rows = rows[:limit]
    last = [_plain(getattr(rows[-1], k)) for k in keys]
    return Page(rows, _write_cursor(spec, fingerprint, last))


def _after(keys: list[str], values: list[object], descending: bool) -> Q:
    """Rows strictly after ``values`` in the (keys) order: an expanded row comparison, plus a
    redundant bound on the first key so an index on it can be used."""
    op = "lt" if descending else "gt"
    condition = Q()
    for i in range(len(keys)):
        equal_before = Q(**{keys[j]: values[j] for j in range(i)})
        condition |= equal_before & Q(**{f"{keys[i]}__{op}": values[i]})
    first_bound = Q(**{f"{keys[0]}__{op}e": values[0]})
    return first_bound & condition


def _write_cursor(spec: ListSpec, fingerprint: str, key: list[object]) -> str:
    return signing.dumps(
        {"v": 1, "e": spec.endpoint, "f": fingerprint, "k": key}, salt=CURSOR_SALT, compress=True
    )


def _read_cursor(raw: str, spec: ListSpec, fingerprint: str, size: int) -> list[object]:
    try:
        data = signing.loads(raw, salt=CURSOR_SALT, max_age=settings.LIST_CURSOR_MAX_AGE_SECONDS)
    except (signing.BadSignature, ValueError, TypeError):  # SignatureExpired is a BadSignature
        raise invalid("cursor") from None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise invalid("cursor")
    key = data.get("k")
    if data.get("e") != spec.endpoint or data.get("f") != fingerprint:
        raise invalid("cursor")
    if not isinstance(key, list) or len(key) != size:
        raise invalid("cursor")
    if not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in key):
        raise invalid("cursor")
    return key


def page_body(page: Page, serialize: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """The response body every list endpoint returns."""
    return {"results": [serialize(row) for row in page.rows], "next_cursor": page.next_cursor}
