"""Password hashing and validation.

Hashes use PBKDF2-HMAC-SHA256 stored as ``pbkdf2_sha256$<iterations>$<salt>$<base64 hash>``. Hashes below
the current iteration count are upgraded transparently at login (``needs_rehash``).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import re
import secrets
from functools import lru_cache
from pathlib import Path

import os

# Work factor for new hashes; Only lowered by tests (min 1000).
PBKDF2_ITERATIONS = max(1000, int(os.environ.get("OPSDESK_PBKDF2_ITERATIONS", "870000") or 870000))
MIN_LENGTH = 12
_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
UNUSABLE_PREFIX = "!"
UNUSABLE_SUFFIX_LENGTH = 40

_COMMON_FILE = Path(__file__).with_name("common_passwords.txt.gz")


def _b64_pbkdf2(algorithm: str, password: str, salt: str, iterations: int) -> str:
    dk = hashlib.pbkdf2_hmac(algorithm, password.encode(), salt.encode(), iterations)
    return base64.b64encode(dk).decode("ascii").strip()


def make_password(password: str | None, *, iterations: int | None = None) -> str:
    """Hash ``password``. ``None`` yields an unusable password (cannot be verified)."""
    if password is None:
        return UNUSABLE_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(UNUSABLE_SUFFIX_LENGTH))
    iterations = iterations or PBKDF2_ITERATIONS
    salt = "".join(secrets.choice(_ALPHABET) for _ in range(22))
    return f"pbkdf2_sha256${iterations}${salt}${_b64_pbkdf2('sha256', password, salt, iterations)}"


def is_password_usable(encoded: str | None) -> bool:
    return encoded is None or not encoded.startswith(UNUSABLE_PREFIX)


def check_password(password: str | None, encoded: str | None) -> bool:
    """Constant-time verification of a PBKDF2-SHA256 hash."""
    if password is None or not encoded or not is_password_usable(encoded):
        return False
    try:
        algorithm, iterations, salt, hash_ = encoded.split("$", 3)
        iterations = int(iterations)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or iterations <= 0:
        return False
    return hmac.compare_digest(_b64_pbkdf2('sha256', password, salt, iterations), hash_)


def needs_rehash(encoded: str) -> bool:
    try:
        algorithm, iterations, *_ = encoded.split("$", 3)
        return algorithm != "pbkdf2_sha256" or int(iterations) < PBKDF2_ITERATIONS
    except ValueError:
        return True


_TIMING_DEFENSE_HASH = make_password("dummy_password_timing_defense")


def burn_hash_time() -> None:
    """Spend one hash verification so unknown accounts take as long as real ones (enumeration defence)."""
    check_password("dummy_password_timing_defense_x", _TIMING_DEFENSE_HASH)


# --------------------------------------------------------------------------- validators
@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    if not _COMMON_FILE.exists():
        return frozenset()
    with gzip.open(_COMMON_FILE, "rt", encoding="utf-8") as fh:
        return frozenset(line.strip() for line in fh if line.strip())


def _similarity_ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(a=a.lower(), b=b.lower()).quick_ratio()


def validate_password(password: str, *, email: str = "", first_name: str = "", last_name: str = "",
                      username: str = "") -> list[str]:
    """Return a list of human readable problems (empty list = acceptable).

    Four rules: minimum length 12, not too similar to user attributes,
    not a commonly used password, not entirely numeric."""
    errors: list[str] = []
    if len(password) < MIN_LENGTH:
        errors.append(f"This password is too short. It must contain at least {MIN_LENGTH} characters.")
    for value in (email, first_name, last_name, username):
        if not value:
            continue
        for part in {value, *re.split(r"\W+", value)}:
            if len(part) >= 1 and (_similarity_ratio(password, part) >= 0.7):
                errors.append("The password is too similar to the user information.")
                break
        else:
            continue
        break
    if password.lower().strip() in _common_passwords():
        errors.append("This password is too common.")
    if password.isdigit():
        errors.append("This password is entirely numeric.")
    return errors
