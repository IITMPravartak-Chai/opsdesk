"""Rules for the read-only Audit Log API (U7-A1): who may read it, which filters are accepted, and what of a stored
event may leave the server. Pure Python (no web framework) so the rules can be tested on their own.

Timezone rule: `since` / `until` are ISO 8601. A value without an offset is taken as UTC; every value is converted to
UTC. `since` is inclusive and `until` is exclusive. A plain date (YYYY-MM-DD) means midnight UTC on that day.
"""
import re
from datetime import datetime, timedelta, timezone

READ_ROLES = ("super_admin", "manager")  # provisional owner decision (U7-0 D-5): managers only

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_RANGE = timedelta(days=366)

_ACTION = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_ACTION_PREFIX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*\.?$")
_OBJECT_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_OBJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Stored change payloads are copied through these limits. Key names that look like credentials are never returned.
SENSITIVE_KEY = re.compile(r"(passw|secret|token|otp|mfa|recovery|api[_-]?key|authori[sz]ation|cookie|session|credential)", re.I)
MAX_STRING = 200
MAX_ITEMS = 50
MAX_DEPTH = 4
REDACTED = "[redacted]"


class InvalidFilter(ValueError):
    """A query parameter failed validation; `field` names it for the 400 response."""

    def __init__(self, field):
        super().__init__(field)
        self.field = field


def can_read(role):
    return role in READ_ROLES


def _parse_instant(value, field):
    raw = (value or "").strip()
    if not raw or len(raw) > 40:
        raise InvalidFilter(field)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            dt = datetime.fromisoformat(raw + "T00:00:00")
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise InvalidFilter(field)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_filters(params):
    """Validate the list endpoint's filters. Returns ORM lookups; raises InvalidFilter on the first bad value.

    `params` is a mapping of the raw query values (None when absent)."""
    lookups = {}
    action = params.get("action")
    if action is not None:
        if not (1 <= len(action) <= 80 and _ACTION.match(action)):
            raise InvalidFilter("action")
        lookups["action"] = action
    prefix = params.get("action_prefix")
    if prefix is not None:
        if not (1 <= len(prefix) <= 80 and _ACTION_PREFIX.match(prefix)):
            raise InvalidFilter("action_prefix")
        lookups["action__startswith"] = prefix
    actor = params.get("actor")
    if actor is not None:
        if not (str(actor).isdigit() and 0 < int(actor) < 2**63):
            raise InvalidFilter("actor")
        lookups["actor_id"] = int(actor)
    object_type = params.get("object_type")
    if object_type is not None:
        if not _OBJECT_TYPE.match(object_type):
            raise InvalidFilter("object_type")
        lookups["object_type"] = object_type
    object_id = params.get("object_id")
    if object_id is not None:
        if not _OBJECT_ID.match(object_id):
            raise InvalidFilter("object_id")
        lookups["object_id"] = object_id
    since = _parse_instant(params["since"], "since") if params.get("since") is not None else None
    until = _parse_instant(params["until"], "until") if params.get("until") is not None else None
    if since and until:
        if until <= since:
            raise InvalidFilter("until")
        if until - since > MAX_RANGE:
            raise InvalidFilter("until")
    if since:
        lookups["created_at__gte"] = since
    if until:
        lookups["created_at__lt"] = until
    return lookups


def parse_page(page, page_size):
    """Bounded page arithmetic. Returns (page, page_size, offset); raises InvalidFilter."""
    try:
        p = int(page) if page is not None else 1
    except (TypeError, ValueError):
        raise InvalidFilter("page")
    try:
        size = int(page_size) if page_size is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        raise InvalidFilter("page_size")
    if p < 1 or p > 100000:
        raise InvalidFilter("page")
    if size < 1 or size > MAX_PAGE_SIZE:
        raise InvalidFilter("page_size")
    return p, size, (p - 1) * size


def sanitize(value, depth=0):
    """Copy a stored change payload through size, depth and credential limits.

    Returns (safe_value, truncated). Strings longer than MAX_STRING are cut, collections are capped at MAX_ITEMS,
    nesting deeper than MAX_DEPTH is replaced, and any key that looks like a credential has its value redacted."""
    if depth > MAX_DEPTH:
        return "…", True
    if isinstance(value, str):
        return (value[:MAX_STRING] + "…", True) if len(value) > MAX_STRING else (value, False)
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, dict):
        out, cut = {}, len(value) > MAX_ITEMS
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_ITEMS:
                break
            key = str(k)[:80]
            if SENSITIVE_KEY.search(key):
                out[key] = REDACTED
                cut = True
                continue
            out[key], c = sanitize(v, depth + 1)
            cut = cut or c
        return out, cut
    if isinstance(value, (list, tuple)):
        out, cut = [], len(value) > MAX_ITEMS
        for v in list(value)[:MAX_ITEMS]:
            s, c = sanitize(v, depth + 1)
            out.append(s)
            cut = cut or c
        return out, cut
    return str(value)[:MAX_STRING], True


def page_links(base_url, query, page, page_size, count):
    """DRF-style next/previous links that keep the caller's filters (never a tenant or role parameter)."""
    from urllib.parse import urlencode
    keep = {k: v for k, v in query.items() if v is not None and k not in ("page", "page_size")}

    def link(p):
        return f"{base_url}?{urlencode({**keep, 'page': p, 'page_size': page_size})}"
    last = max(1, -(-count // page_size))
    return (link(page + 1) if page < last else None, link(page - 1) if page > 1 else None)
