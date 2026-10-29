"""U7-A1: rules of the read-only Audit Log API, tested without a web framework or a database.

Run from backend/:  python -m pytest -q tests/unit
(The database-backed API, authorization and tenant-isolation tests are in tests/test_api_admin_people_sla.py.)
"""
import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opsdesk.api import audit_view as av

ROUTER = Path(__file__).resolve().parents[2] / "opsdesk" / "api" / "routers" / "admin_audit.py"


# ---------------------------------------------------------------- who may read
@pytest.mark.parametrize("role,allowed", [("manager", True), ("team_lead", False), ("employee", False),
                                          ("customer", False), ("", False), (None, False)])
def test_only_administrators_may_read(role, allowed):
    assert av.can_read(role) is allowed


# ---------------------------------------------------------------- filters
def test_valid_filters_become_lookups():
    got = av.parse_filters({"action": "ticket.update", "action_prefix": "ticket.", "actor": "42",
                            "object_type": "Ticket", "object_id": "1007", "since": "2026-10-01", "until": "2026-10-23T12:00:00+05:30"})
    assert got["action"] == "ticket.update" and got["action__startswith"] == "ticket." and got["actor_id"] == 42
    assert got["object_type"] == "Ticket" and got["object_id"] == "1007"
    assert got["created_at__gte"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert got["created_at__lt"] == datetime(2026, 10, 23, 6, 30, tzinfo=timezone.utc)  # converted to UTC


def test_no_offset_means_utc_and_z_is_accepted():
    a = av.parse_filters({"since": "2026-10-23T08:00:00"})["created_at__gte"]
    b = av.parse_filters({"since": "2026-10-23T08:00:00Z"})["created_at__gte"]
    assert a == b == datetime(2026, 10, 23, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("params,field", [
    ({"action": "Ticket.Update"}, "action"), ({"action": "ticket..update"}, "action"), ({"action": "a" * 81}, "action"),
    ({"action": "ticket.update'; drop"}, "action"), ({"action_prefix": ".ticket"}, "action_prefix"),
    ({"actor": "abc"}, "actor"), ({"actor": "0"}, "actor"), ({"actor": "-3"}, "actor"), ({"actor": "9" * 30}, "actor"),
    ({"object_type": "1Ticket"}, "object_type"), ({"object_type": "Ti cket"}, "object_type"),
    ({"object_id": "../etc"}, "object_id"), ({"object_id": "x" * 65}, "object_id"),
    ({"since": "yesterday"}, "since"), ({"since": ""}, "since"), ({"until": "2026-13-01"}, "until"),
    ({"since": "2026-10-23", "until": "2026-10-23"}, "until"), ({"since": "2026-10-23", "until": "2026-10-22"}, "until"),
    ({"since": "2025-01-01", "until": "2026-10-23"}, "until"),
])
def test_invalid_filters_are_rejected_with_the_field_name(params, field):
    with pytest.raises(av.InvalidFilter) as e:
        av.parse_filters(params)
    assert e.value.field == field


def test_no_filter_can_name_a_tenant():
    # the tenant is never a parameter; unknown keys are simply not read
    assert av.parse_filters({"tenant": "2", "tenant_id": "2"}) == {}


# ---------------------------------------------------------------- pagination
def test_page_bounds():
    assert av.parse_page(None, None) == (1, 50, 0)
    assert av.parse_page("3", "20") == (3, 20, 40)
    assert av.parse_page("1", "100") == (1, 100, 0)
    for page, size, field in (("0", "10", "page"), ("x", "10", "page"), ("1", "101", "page_size"),
                              ("1", "0", "page_size"), ("1", "x", "page_size"), ("100001", "10", "page")):
        with pytest.raises(av.InvalidFilter) as e:
            av.parse_page(page, size)
        assert e.value.field == field


def test_page_links_keep_filters_but_never_add_tenant():
    nxt, prev = av.page_links("https://h/api/admin/audit-logs/", {"action": "auth.login", "since": None, "page": "2"}, 2, 10, 25)
    assert nxt == "https://h/api/admin/audit-logs/?action=auth.login&page=3&page_size=10"
    assert prev == "https://h/api/admin/audit-logs/?action=auth.login&page=1&page_size=10"
    assert av.page_links("u", {}, 3, 10, 25) == (None, "u?page=2&page_size=10")
    assert av.page_links("u", {}, 1, 10, 0) == (None, None)


# ---------------------------------------------------------------- what leaves the server
def test_sanitize_redacts_credential_keys_anywhere():
    stored = {"state": ["New", "Open"], "password": "p", "nested": {"mfa_secret": "S", "API-Key": "k", "ok": 1},
              "items": [{"refresh_token": "t"}], "Authorization": "Bearer x", "recovery_codes": ["1"], "otp": "123456"}
    out, cut = av.sanitize(stored)
    assert out["state"] == ["New", "Open"] and out["nested"]["ok"] == 1
    for k in ("password", "Authorization", "recovery_codes", "otp"):
        assert out[k] == av.REDACTED
    assert out["nested"]["mfa_secret"] == av.REDACTED and out["nested"]["API-Key"] == av.REDACTED
    assert out["items"][0]["refresh_token"] == av.REDACTED
    assert cut is True
    flat = repr(out)
    for secret in ("'p'", "'S'", "'k'", "Bearer x", "123456"):
        assert secret not in flat


def test_sanitize_truncates_text_and_caps_size_and_depth():
    long = "x" * 500
    out, cut = av.sanitize({"description": [long, "short"]})
    assert out["description"][0] == "x" * 200 + "…" and out["description"][1] == "short" and cut
    out, cut = av.sanitize({f"k{i}": i for i in range(80)})
    assert len(out) == av.MAX_ITEMS and cut
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    out, cut = av.sanitize(deep)
    assert cut and "…" in repr(out)
    assert av.sanitize({"tags": [["a"], ["b"]], "n": 3, "flag": True, "none": None}) == (
        {"tags": [["a"], ["b"]], "n": 3, "flag": True, "none": None}, False)


# ---------------------------------------------------------------- the router has no write operation
def _routes():
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and getattr(dec.func.value, "id", "") == "router":
                    yield dec.func.attr, dec.args[0].value if dec.args else None


def test_router_exposes_only_get_routes():
    routes = list(_routes())
    assert routes and all(method == "get" for method, _ in routes), routes
    assert sorted(p for _, p in routes) == ["/", "/actions/"]
    source = ROUTER.read_text(encoding="utf-8")
    for forbidden in (".delete(", ".update(", ".create(", ".save(", "api_route", "ip_address\""):
        assert forbidden not in source, forbidden


def test_router_enforces_role_and_tenant_in_every_handler():
    source = ROUTER.read_text(encoding="utf-8")
    assert source.count("_require_reader(user)") == 2
    assert source.count("run_in_tenant(user,") == 2  # every query runs inside the caller's signed tenant session
    assert source.count("AuditLog.tenant_id == user.tenant_id") == 2  # explicit tenant filter on top of row-level security
    assert source.count("Depends(get_current_user)") == 2
