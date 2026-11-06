"""Known-answer tests for the security primitives: stored password hashes, encrypted channel configs and tokens
keep verifying/decrypting across releases (the vectors below are fixed, previously generated values)."""
import json
import time

import jwt
import pytest

from opsdesk.security import passwords, tokens
from opsdesk.settings import settings

# Fixed vector: password "Demo!Passw0rd2026"
KNOWN_HASH = "pbkdf2_sha256$870000$MXjQ6a4mbyq1Q13QRMeJpb$Js6JFwblvKv3DGy4riWP/jPAvpn2tA2NsuUhnqbuKZE="
# Fixed vector encrypted with the development fallback key (sha256 of the default secret)
KNOWN_CIPHERTEXT = (
    "gAAAAABq3wKTkLS_Qj-sxzrtaLUk_1C9zEkUs3w9lq_COYdFFzDlXMBnndSVLi1uHiWuj5eaiZvIwFDSufi9HGnY8apxkLPqOCaBKfkohlMr-EWfw_"
    "iGKtkg7_vNpwmrepDfcjZNh1gPgrfwKwr0h72qaZasNfGcF3nNrQ6zR7bg6nx2QZwLOmnJy18NFqi5QoLFiWziez-cdxYSoLv3zMXlmRciijwbZQ==")


class TestPasswords:
    def test_verifies_known_hash(self):
        assert passwords.check_password("Demo!Passw0rd2026", KNOWN_HASH)

    def test_rejects_wrong_password_and_garbage(self):
        assert not passwords.check_password("wrong", KNOWN_HASH)
        for bad in ("", None, "plain", "pbkdf2_sha256$x$y", "md5$salt$hash", "argon2$whatever"):
            assert not passwords.check_password("Demo!Passw0rd2026", bad)

    def test_unusable_password_never_verifies(self):
        unusable = passwords.make_password(None)
        assert unusable.startswith("!") and not passwords.is_password_usable(unusable)
        assert not passwords.check_password("anything", unusable)
        assert not passwords.check_password("", unusable)

    def test_new_hashes_use_the_documented_format_and_roundtrip(self):
        h = passwords.make_password("Another!Passw0rd2026")
        algo, iterations, salt, digest = h.split("$")
        assert algo == "pbkdf2_sha256" and int(iterations) == passwords.PBKDF2_ITERATIONS and len(salt) == 22
        assert passwords.check_password("Another!Passw0rd2026", h)
        assert h != passwords.make_password("Another!Passw0rd2026")  # salted

    def test_rehash_detection(self, monkeypatch):
        monkeypatch.setattr(passwords, "PBKDF2_ITERATIONS", 870_000)
        assert not passwords.needs_rehash(KNOWN_HASH)
        weak = passwords.make_password("x" * 12, iterations=1000)
        assert passwords.needs_rehash(weak)

    @pytest.mark.parametrize("pw,expected", [
        ("short1!", "too short"), ("password1234", "too common"), ("123456789012", "entirely numeric"),
    ])
    def test_policy_rejections(self, pw, expected):
        assert any(expected in p for p in passwords.validate_password(pw))

    def test_policy_rejects_similarity_to_identity(self):
        assert any("similar" in p for p in passwords.validate_password("ada.lovelace@example.com", email="ada.lovelace@example.com"))

    def test_policy_accepts_a_strong_password(self):
        assert passwords.validate_password("Xk9!vQ2#mLw8pZ", email="ada@example.com") == []


class TestEncryptedChannelConfig:
    def test_decrypts_known_ciphertext(self):
        from opsdesk.db.base import decrypt_json
        cfg = decrypt_json(KNOWN_CIPHERTEXT)
        assert cfg["provider"] == "generic_json" and cfg["webhook_token"] == "demo-email-secret"

    def test_roundtrip_and_rotation(self, monkeypatch):
        from cryptography.fernet import Fernet

        from opsdesk.db import base
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        monkeypatch.setattr(settings, "field_encryption_keys", [old])
        token = base.encrypt_json({"password": "s3cret"})
        monkeypatch.setattr(settings, "field_encryption_keys", [new, old])  # rotation: newest first
        assert base.decrypt_json(token) == {"password": "s3cret"}
        assert base.decrypt_json(base.encrypt_json({"a": 1})) == {"a": 1}

    def test_unreadable_ciphertext_yields_empty_config(self):
        from opsdesk.db.base import decrypt_json
        assert decrypt_json("not-a-token") == {} and decrypt_json("") == {} and decrypt_json(None) == {}


def _claims(**over):
    now = int(time.time())
    c = {"token_type": "access", "iat": now, "exp": now + 600, "jti": "abc", "user_id": "7", "tid": 3, "role": "employee"}
    c.update(over)
    return c


def _sign(claims, key=None):
    return jwt.encode(claims, key or settings.jwt_secret_key, algorithm="HS256")


class TestTokens:
    def test_accepts_access_token_with_string_user_id(self):
        p = tokens.decode_access(_sign(_claims()))
        assert p["user_id"] == 7 and p["tid"] == 3 and p["role"] == "employee"

    def test_accepts_integer_user_id_from_older_tokens(self):
        assert tokens.decode_access(_sign(_claims(user_id=7)))["user_id"] == 7

    @pytest.mark.parametrize("bad", [True, 0, -1, "0", "007", "+7", "7.0", "٧", "9" * 20, None, [], {}])
    def test_user_id_claim_is_strict(self, bad):
        assert tokens.normalize_user_id(bad) is None

    def test_rejects_refresh_token_as_access(self):
        with pytest.raises(tokens.TokenError):
            tokens.decode_access(_sign(_claims(token_type="refresh")))

    def test_rejects_expired_wrong_key_and_unsigned(self):
        with pytest.raises(tokens.TokenError):
            tokens.decode_access(_sign(_claims(exp=int(time.time()) - 5)))
        with pytest.raises(tokens.TokenError):
            tokens.decode_access(_sign(_claims(), key="another-key-another-key-another-key!!"))
        with pytest.raises(tokens.TokenError):
            tokens.decode_access(jwt.encode(_claims(), key="", algorithm="none"))

    def test_refresh_to_access_carries_claims_and_fresh_role(self):
        refresh, claims = tokens.issue_refresh(5, 9, "employee")
        decoded = tokens.decode_refresh(refresh)
        assert decoded["user_id"] == 5 and decoded["tid"] == 9
        access = tokens.decode_access(tokens.issue_access_from(claims, role="team_lead"))
        assert access["user_id"] == 5 and access["tid"] == 9 and access["role"] == "team_lead"
        assert access["jti"] != claims["jti"]

    def test_revoked_refresh_token_is_refused(self):
        refresh, claims = tokens.issue_refresh(5, 9, "employee")
        tokens.blacklist(claims)
        tokens.blacklist(claims)  # idempotent
        with pytest.raises(tokens.TokenError):
            tokens.decode_refresh(refresh)
