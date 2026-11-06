"""Sign-in rate limiting module: fixed-window counters in the shared Redis store."""
import time
from dataclasses import replace

import pytest
import redis

from opsdesk.security import login_throttle as lt
from tests.conftest import REDIS_URL


@pytest.fixture
def r():
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    client.flushdb()
    return client


def cfg(**kw):
    return replace(lt.CONFIG, account_limit=3, account_window=5, source_limit=5, source_window=5, rate_limit=50, rate_window=5, **kw)


def attempt(r, email="a@x.test", addr="1.2.3.4", config=None):
    t = lt.LoginThrottle(r, "secret", email, addr, config or cfg())
    t.reserve()
    return t


def test_account_limit_blocks_the_next_attempt_with_a_retry_time(r):
    for _ in range(3):
        attempt(r)
    with pytest.raises(lt.Throttled) as e:
        attempt(r)
    assert 1 <= e.value.retry_after <= 5 and "Try again in about 1 minute" in e.value.detail


def test_counters_are_per_account_and_case_insensitive(r):
    for _ in range(3):
        attempt(r, "A@X.test")
    with pytest.raises(lt.Throttled):
        attempt(r, "a@x.test")
    attempt(r, "other@x.test")


def test_source_failures_limit_across_many_accounts(r):
    for i in range(5):
        attempt(r, f"user{i}@x.test")
    with pytest.raises(lt.Throttled):
        attempt(r, "user99@x.test")
    attempt(r, "user99@x.test", addr="9.9.9.9")  # another source is unaffected


def test_a_successful_sign_in_clears_the_account_and_refunds_the_source(r):
    t1 = attempt(r)
    t1.succeeded()
    assert r.get(t1.keys["account"]) is None and int(r.get(t1.keys["source"])) == 0
    for _ in range(3):
        attempt(r)


def test_waiting_for_the_two_step_code_is_not_a_failed_attempt(r):
    for _ in range(10):
        t = attempt(r)
        t.challenged()
    attempt(r)


def test_windows_expire_and_are_not_extended_by_later_attempts(r):
    c = replace(cfg(), account_window=1)
    for _ in range(3):
        attempt(r, config=c)
    with pytest.raises(lt.Throttled):
        attempt(r, config=c)
    time.sleep(1.2)
    attempt(r, config=c)


def test_keys_hold_only_keyed_hashes_never_the_email_or_address(r):
    t = attempt(r, "secret.person@corp.test", "203.0.113.9")
    blob = " ".join(t.keys.values())
    assert "secret.person" not in blob and "203.0.113.9" not in blob and "corp.test" not in blob


def test_store_outage_fails_closed():
    dead = redis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    with pytest.raises(lt.StoreUnavailable):
        lt.LoginThrottle(dead, "secret", "a@x.test", "1.1.1.1", cfg()).reserve()


@pytest.mark.parametrize("peer,xff,expected", [
    ("10.0.0.5", "203.0.113.7", "203.0.113.7"),                 # trusted proxy: the last forwarded hop
    ("10.0.0.5", "6.6.6.6, 203.0.113.7", "203.0.113.7"),        # client-supplied entries sit further left and are ignored
    ("8.8.8.8", "6.6.6.6", "8.8.8.8"),                           # untrusted peer: the header is ignored entirely
    ("10.0.0.5", "", "10.0.0.5"), ("10.0.0.5", "not-an-ip", "10.0.0.5"), ("", None, "unknown"),
])
def test_client_address_trusts_forwarding_headers_only_from_proxies(peer, xff, expected):
    assert lt.client_address(peer, xff) == expected


def test_two_hops_behind_an_extra_load_balancer():
    c = replace(lt.CONFIG, trusted_hops=2)
    assert lt.client_address("10.0.0.5", "203.0.113.7, 10.1.1.1", c) == "203.0.113.7"
    assert lt.client_address("10.0.0.5", "10.1.1.1", c) == "10.0.0.5"  # not enough hops: fall back to the peer
