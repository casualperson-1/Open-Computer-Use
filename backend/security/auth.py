"""Authentication primitives: password hashing, sessions, CSRF and throttling.

Passwords are hashed with scrypt using a per-password random salt. Session and
CSRF secrets are generated with ``secrets`` and compared in constant time.
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
from typing import Deque, Dict, Iterable

from config import settings

SESSION_COOKIE = "ocu_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

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


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_DKLEN,
        maxmem=max(SCRYPT_MAXMEM, 2 * 128 * n * r),
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${_b64(salt)}${_b64(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = (encoded or "").split("$")
        if scheme != "scrypt":
            return False
        n_i, r_i, p_i = int(n), int(r), int(p)
        if not (2**14 <= n_i <= 2**18 and 1 <= r_i <= 16 and 1 <= p_i <= 4):
            return False
        salt_bytes = _unb64(salt)
        expected_bytes = _unb64(expected)
        digest = _scrypt(password, salt_bytes, n_i, r_i, p_i)
        return hmac.compare_digest(digest, expected_bytes)
    except (ValueError, TypeError, MemoryError):
        return False


def needs_rehash(encoded: str) -> bool:
    try:
        scheme, n, r, p, _, _ = (encoded or "").split("$")
        return scheme != "scrypt" or (
            int(n), int(r), int(p)
        ) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    except (ValueError, TypeError):
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


class AttemptThrottle:
    """Small in-process sliding-window limiter for login and signup attempts."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window_seconds))
        self._attempts: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        bucket = self._attempts.setdefault(key, deque())
        cutoff = now - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def retry_after(self, *keys: str) -> int:
        now = time.monotonic()
        wait = 0.0
        with self._lock:
            for key in keys:
                bucket = self._prune(key, now)
                if len(bucket) >= self.limit:
                    wait = max(wait, bucket[0] + self.window - now)
        return max(0, int(wait + 0.999)) if wait > 0 else 0

    def fail(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._attempts) > 2048:
                for stale in [k for k, v in self._attempts.items() if not v or v[-1] <= now - self.window]:
                    self._attempts.pop(stale, None)
            for key in keys:
                bucket = self._prune(key, now)
                bucket.append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._attempts.pop(key, None)


# Used to equalise timing when a username does not exist.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))
