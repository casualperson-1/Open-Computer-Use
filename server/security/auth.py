"""Authentication primitives: password hashing, sessions, CSRF and lockouts.

Passwords are hashed with **argon2id** (``argon2-cffi``). Hashes written by
older builds with scrypt still verify and are upgraded on the next successful
sign-in. Session and CSRF values come from ``secrets`` and are compared in
constant time.

Failed logins are throttled per IP, per username and globally, with an
exponential lockout, so guessing is both rate limited and increasingly slow.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from typing import Deque, Dict

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from config import settings

SESSION_COOKIE = "ocu_session"
SESSION_TTL_SECONDS = 90 * 24 * 60 * 60

# argon2id with the OWASP-recommended profile (19 MiB is the floor; 64 MiB with
# 3 passes is comfortable on a homelab box and cheap next to an LLM call).
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,
)

# Legacy scrypt parameters, kept only to verify hashes made before argon2id.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 128 * 1024 * 1024

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
COMMON_PASSWORDS = {
    "123456789012",
    "password1234",
    "passwordpassword",
    "qwertyuiop12",
    "letmein12345",
    "admin1234567",
    "welcome12345",
    "iloveyou1234",
}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hash with argon2id; the encoded hash carries its own parameters."""
    return HASHER.hash(password)


def _verify_scrypt(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = (encoded or "").split("$")
        if scheme != "scrypt":
            return False
        n_i, r_i, p_i = int(n), int(r), int(p)
        if not (2**14 <= n_i <= 2**18 and 1 <= r_i <= 16 and 1 <= p_i <= 4):
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=n_i,
            r=r_i,
            p=p_i,
            dklen=SCRYPT_DKLEN,
            maxmem=max(SCRYPT_MAXMEM, 2 * 128 * n_i * r_i),
        )
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError, MemoryError):
        return False


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time-ish verification for argon2id and legacy scrypt hashes."""
    value = encoded or ""
    if value.startswith("$argon2"):
        try:
            return bool(HASHER.verify(value, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except Exception:  # pragma: no cover - defensive
            return False
    return _verify_scrypt(password, value)


def needs_rehash(encoded: str) -> bool:
    """True for legacy scrypt hashes and for argon2 params weaker than ours."""
    value = encoded or ""
    if value.startswith("$argon2"):
        try:
            return bool(HASHER.check_needs_rehash(value))
        except (InvalidHashError, VerificationError):
            return True
        except Exception:  # pragma: no cover - defensive
            return True
    return True


def normalize_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Username must be 3-32 characters: lowercase letters, numbers, dot, dash or underscore."
        )
    return username


def validate_password(password: str, username: str = "") -> None:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters.")
    if not password.strip():
        raise ValueError("Password cannot be only whitespace.")
    if any(ord(char) < 32 for char in password):
        raise ValueError("Password cannot contain control characters.")
    lowered = password.lower()
    if username and username in lowered:
        raise ValueError("Password cannot contain the username.")
    if lowered in COMMON_PASSWORDS:
        raise ValueError("That password is too common. Choose a longer passphrase.")


# ---------------------------------------------------------------------------
# Sessions and CSRF
# ---------------------------------------------------------------------------


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def csrf_token_for(session_token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ("csrf:" + (session_token or "")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def csrf_matches(session_token: str, provided: str) -> bool:
    return bool(provided) and hmac.compare_digest(
        csrf_token_for(session_token), provided
    )


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------


class LoginThrottle:
    """Failed-login throttle with exponential lockout.

    Failures are counted inside a sliding ``window`` per key. Once ``limit`` is
    reached, the key locks for ``base_lock * 2 ** (failures - limit)`` seconds,
    capped at ``max_lock``. Pass several keys (IP, username, ``"global"``) to
    apply all the limits at once.
    """

    def __init__(
        self,
        limit: int = 5,
        window: float = 300.0,
        base_lock: float = 5.0,
        max_lock: float = 900.0,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window))
        self.base_lock = max(1.0, float(base_lock))
        self.max_lock = max(self.base_lock, float(max_lock))
        self._attempts: Dict[str, Deque[float]] = {}
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        bucket = self._attempts.setdefault(key, deque())
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def locked_for(self, *keys: str) -> int:
        """Seconds the caller must wait, based on any of the keys."""
        now = time.monotonic()
        wait = 0.0
        with self._lock:
            for key in keys:
                until = self._locked_until.get(key, 0.0)
                if until > now:
                    wait = max(wait, until - now)
                elif until:
                    self._locked_until.pop(key, None)
        return int(wait + 0.999) if wait > 0 else 0

    def fail(self, *keys: str) -> int:
        """Record one failure; returns the new lock duration in seconds."""
        now = time.monotonic()
        lock_for = 0.0
        with self._lock:
            if len(self._attempts) > 2048:
                for stale in [
                    key
                    for key, bucket in self._attempts.items()
                    if not bucket or bucket[-1] <= now - self.window
                ]:
                    self._attempts.pop(stale, None)
                    self._locked_until.pop(stale, None)
            for key in keys:
                bucket = self._prune(key, now)
                bucket.append(now)
                streak = len(bucket)
                if streak >= self.limit:
                    seconds = min(
                        self.max_lock, self.base_lock * 2 ** (streak - self.limit)
                    )
                    self._locked_until[key] = now + seconds
                    lock_for = max(lock_for, seconds)
        return int(lock_for + 0.999) if lock_for > 0 else 0

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._attempts.pop(key, None)
                self._locked_until.pop(key, None)


# Equalises timing when a username does not exist.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


__all__ = [
    "ARGON2_MEMORY_COST",
    "ARGON2_TIME_COST",
    "COMMON_PASSWORDS",
    "DUMMY_PASSWORD_HASH",
    "LoginThrottle",
    "SESSION_COOKIE",
    "SESSION_TTL_SECONDS",
    "csrf_matches",
    "csrf_token_for",
    "hash_password",
    "hash_session_token",
    "needs_rehash",
    "new_session_token",
    "normalize_username",
    "validate_password",
    "verify_password",
]
