"""One-time codes by email: issue, verify, cooldown, wrong-try limit and what the store holds."""
import time
from dataclasses import replace

import pytest
import redis

from opsdesk.security import email_otp as eo
from tests.conftest import REDIS_URL

SECRET = "secret"


@pytest.fixture
def r():
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.ping()
    client.flushdb()
    return client


def cfg(**kw):
    return replace(eo.CONFIG, **{"ttl": 30, "resend": 30, "max_tries": 3, **kw})


def other(code):
    return f"{(int(code) + 1) % 10 ** 6:06d}"


def test_a_code_is_six_digits_and_verifies_once(r):
    code = eo.issue(r, SECRET, eo.LOGIN, 7, cfg())
    assert len(code) == 6 and code.isdigit()
    assert eo.verify(r, SECRET, eo.LOGIN, 7, f" {code} ", cfg()) is True
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, cfg()) is False


def test_codes_are_bound_to_the_person_and_the_purpose(r):
    code = eo.issue(r, SECRET, eo.LOGIN, 7, cfg())
    assert eo.verify(r, SECRET, eo.LOGIN, 8, code, cfg()) is False
    assert eo.verify(r, SECRET, eo.DISABLE, 7, code, cfg()) is False
    assert eo.verify(r, "another-secret", eo.LOGIN, 7, code, cfg()) is False
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, cfg()) is True


@pytest.mark.parametrize("bad", ["", None, "12345", "1234567", "abcdef", "12 456"])
def test_malformed_codes_never_verify(r, bad):
    eo.issue(r, SECRET, eo.LOGIN, 7, cfg())
    assert eo.verify(r, SECRET, eo.LOGIN, 7, bad, cfg()) is False


def test_no_new_code_during_the_cooldown_and_the_old_one_keeps_working(r):
    code = eo.issue(r, SECRET, eo.LOGIN, 7, cfg())
    assert eo.issue(r, SECRET, eo.LOGIN, 7, cfg()) is None
    assert eo.issue(r, SECRET, eo.ENABLE, 7, cfg()) is not None   # another purpose has its own cooldown
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, cfg()) is True


def test_after_the_cooldown_a_new_code_replaces_the_old_one(r):
    c = cfg(resend=1)
    first = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    time.sleep(1.2)
    second = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    assert second is not None
    if first != second:
        assert eo.verify(r, SECRET, eo.LOGIN, 7, first, c) is False
    assert eo.verify(r, SECRET, eo.LOGIN, 7, second, c) is True


def test_codes_expire(r):
    c = cfg(ttl=1)
    code = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    time.sleep(1.2)
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, c) is False


def test_too_many_wrong_tries_drop_the_code_and_a_new_code_buys_no_more_guesses(r):
    c = cfg(resend=1)
    code = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    for _ in range(3):
        assert eo.verify(r, SECRET, eo.LOGIN, 7, other(code), c) is False
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, c) is False
    time.sleep(1.2)
    fresh = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    assert eo.verify(r, SECRET, eo.LOGIN, 7, fresh, c) is False   # still inside the wrong-try window


def test_a_correct_code_clears_the_wrong_try_count(r):
    c = cfg()
    code = eo.issue(r, SECRET, eo.LOGIN, 7, c)
    for _ in range(2):
        eo.verify(r, SECRET, eo.LOGIN, 7, other(code), c)
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, c) is True
    assert r.get(eo.keys(eo.LOGIN, 7)["tries"]) is None and r.get(eo.keys(eo.LOGIN, 7)["cooldown"]) is None


def test_the_store_holds_a_keyed_hash_never_the_code(r):
    code = eo.issue(r, SECRET, eo.LOGIN, 7, cfg())
    stored = {k: r.get(k) for k in r.keys("*")}
    assert stored and all(code not in k and code != v for k, v in stored.items())
    assert 0 < r.ttl(eo.keys(eo.LOGIN, 7)["code"]) <= 30


def test_send_mails_the_code_and_a_delivery_failure_discards_it(r, monkeypatch):
    sent = []
    monkeypatch.setattr(eo.mailer, "send_mail", lambda subject, body, recipients, **kw: sent.append((subject, body, recipients)))
    assert eo.send(r, SECRET, eo.LOGIN, 7, "a@x.test", cfg()) is True
    subject, body, recipients = sent[0]
    assert recipients == ["a@x.test"] and "sign-in code" in subject
    code = next(w for w in body.split() if len(w) == 6 and w.isdigit())
    assert eo.send(r, SECRET, eo.LOGIN, 7, "a@x.test", cfg()) is False and len(sent) == 1   # cooldown
    assert eo.verify(r, SECRET, eo.LOGIN, 7, code, cfg()) is True

    def broken(*a, **kw):
        raise OSError("down")
    monkeypatch.setattr(eo.mailer, "send_mail", broken)
    with pytest.raises(eo.DeliveryFailed):
        eo.send(r, SECRET, eo.LOGIN, 9, "b@x.test", cfg())
    assert r.keys(f"{eo.KEY_PREFIX}:{eo.LOGIN}:9:*") == []   # nothing left behind, so asking again works at once


def test_store_outage_fails_closed():
    dead = redis.Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, decode_responses=True)
    with pytest.raises(eo.StoreUnavailable):
        eo.issue(dead, SECRET, eo.LOGIN, 7)
    with pytest.raises(eo.StoreUnavailable):
        eo.verify(dead, SECRET, eo.LOGIN, 7, "123456")


def test_mask_email():
    assert eo.mask_email("sreegousalya.v@corp.example") == "s***@corp.example"
    assert eo.mask_email("") == "your email" and eo.mask_email("nodomain") == "your email"


def test_settings_that_are_not_positive_whole_numbers_fall_back_to_the_default(monkeypatch):
    monkeypatch.setenv("OPSDESK_TEST_LIMIT", "600")
    assert eo._int("OPSDESK_TEST_LIMIT", 7) == 600
    for bad in ("soon", "", "0", "-5", "1.5"):
        monkeypatch.setenv("OPSDESK_TEST_LIMIT", bad)
        assert eo._int("OPSDESK_TEST_LIMIT", 7) == 7, bad  # a bad or zero value never switches a control off
    monkeypatch.delenv("OPSDESK_TEST_LIMIT")
    assert eo._int("OPSDESK_TEST_LIMIT", 7) == 7


def test_a_code_used_by_a_parallel_request_does_not_verify_twice(r):
    """Both requests read the code, but only the one that deletes it wins."""
    code = eo.issue(r, SECRET, eo.LOGIN, 7, cfg())

    class LosesTheRace:
        def __getattr__(self, name):
            return getattr(r, name)

        def delete(self, *keys):
            r.delete(*keys)
            return 0  # somebody else already removed it

    assert eo.verify(LosesTheRace(), SECRET, eo.LOGIN, 7, code, cfg()) is False


def test_discard_never_raises_when_the_store_is_down(r, caplog):
    class Down:
        def delete(self, *keys):
            raise redis.RedisError("connection lost")

    with caplog.at_level("ERROR", logger="opsdesk.email_otp"):
        eo.discard(Down(), eo.LOGIN, 7)  # the mail already failed: a second failure must not hide it
    assert "email code discard failed: RedisError" in caplog.text
