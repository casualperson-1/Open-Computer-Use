"""Allowlists, rate limiting and audit helpers shared by the server and a node.

Both sides enforce policy with this module, so a rule means the same thing
wherever it is applied. Matching is deliberately small and predictable:
comma-separated glob patterns, case-insensitive.

Domain patterns::

    example.com        exactly that host
    *.example.com      any subdomain of example.com (and example.com itself)
    *                  anything

Audit events never contain typed text or URL query strings; see
:func:`describe_command`.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
from urllib.parse import urlsplit

MAX_PATTERN_LENGTH = 200
MAX_AUDIT_TEXT = 120

COMMAND_EXAMPLES = ("mouse_move", "click", "type", "open_url", "list_apps", "stop")


class PolicyDenied(RuntimeError):
    """Raised when a rule forbids an action (or the rate limit is exhausted)."""

    def __init__(self, message: str, code: str = "not_allowed") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


def split_patterns(value: Any) -> Tuple[str, ...]:
    """Normalise a comma/newline separated list (or an iterable) of patterns."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw: Iterable[Any] = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        raw = [value]

    patterns = []
    for item in raw:
        text = str(item).strip().lower()
        if not text or len(text) > MAX_PATTERN_LENGTH:
            continue
        if text not in patterns:
            patterns.append(text)
    return tuple(patterns)


def matches_any(value: str, patterns: Sequence[str]) -> bool:
    """Glob match one value (command name, app id, …) against patterns."""
    text = (value or "").strip().lower()
    if not text or not patterns:
        return False
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)


def host_of(value: str) -> str:
    """The lower-case host of a URL or bare domain, or ``""``."""
    text = (value or "").strip()
    # A host never contains whitespace; without this check urlsplit happily
    # treats "not a url" as a host and policy checks silently pass.
    if not text or any(character.isspace() for character in text):
        return ""
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlsplit(text)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host


def domain_matches(host: str, patterns: Sequence[str]) -> bool:
    """Match a host against domain patterns. Empty patterns match nothing."""
    name = (host or "").strip().lower().rstrip(".")
    if not name or not patterns:
        return False
    for pattern in patterns:
        if pattern == "*":
            return True
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if name == suffix or name.endswith("." + suffix):
                return True
        elif fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def url_allowed(url: str, allow: Sequence[str], deny: Sequence[str]) -> bool:
    """Apply domain rules to a URL. An empty allow list permits every host."""
    host = host_of(url)
    if not host:
        return False
    if deny and domain_matches(host, deny):
        return False
    if allow and not domain_matches(host, allow):
        return False
    return True


def describe_command(name: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A safe audit summary of a command: no typed text, no URL queries."""
    params = params or {}
    summary: Dict[str, Any] = {"command": name}
    if name == "type":
        summary["characters"] = len(str(params.get("text") or ""))
    elif name in ("open_url",):
        summary["url"] = redact_url(str(params.get("url") or ""))
    elif name in ("open_item",):
        summary["target"] = str(params.get("target") or "")[:MAX_AUDIT_TEXT]
        if params.get("kind"):
            summary["kind"] = str(params["kind"])[:80]
        if params.get("app_id"):
            summary["app_id"] = str(params["app_id"])[:MAX_AUDIT_TEXT]
    elif name == "browser_action":
        summary["action"] = str(params.get("action") or "")[:40]
        if params.get("url"):
            summary["url"] = redact_url(str(params["url"]))
        if params.get("selector"):
            summary["selector"] = str(params["selector"])[:MAX_AUDIT_TEXT]
        if params.get("text") is not None:
            summary["characters"] = len(str(params.get("text") or ""))
        if params.get("keys"):
            summary["keys"] = str(params["keys"])[:40]
    elif name == "key_combo":
        summary["keys"] = str(params.get("keys") or "")[:40]
    elif name == "list_apps":
        if params.get("query"):
            summary["query"] = str(params["query"])[:MAX_AUDIT_TEXT]
    elif name in ("click", "mouse_move"):
        for key in ("x", "y", "button", "clicks"):
            if params.get(key) is not None:
                summary[key] = params[key]
    elif name == "scroll":
        if params.get("amount") is not None:
            summary["amount"] = params["amount"]
    elif name == "drag":
        for key in ("from_x", "from_y", "to_x", "to_y"):
            if params.get(key) is not None:
                summary[key] = params[key]
    return summary


def redact_url(url: str) -> str:
    """Keep scheme, host and path; drop query and fragment."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text[:MAX_AUDIT_TEXT]
    path = parsed.path or ""
    redacted = f"{parsed.scheme}://{parsed.netloc}{path}"
    return redacted[:MAX_AUDIT_TEXT]


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """A token bucket measured in actions per minute.

    ``per_minute <= 0`` disables the limiter (``allow`` always returns True),
    which is the default so existing behaviour is unchanged until an operator
    turns a limit on.
    """

    def __init__(self, per_minute: int = 0, burst: Optional[int] = None) -> None:
        self.per_minute = max(0, int(per_minute))
        self.capacity = float(
            burst if burst is not None else max(1, self.per_minute or 1)
        )
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def _refill(self) -> None:
        now = time.monotonic()
        rate = self.per_minute / 60.0
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * rate)
        self._updated = now

    def allow(self, cost: float = 1.0) -> bool:
        if not self.enabled:
            return True
        with self._lock:
            self._refill()
            if self._tokens < cost:
                return False
            self._tokens -= cost
            return True

    def retry_after(self) -> float:
        """Seconds until one more action is allowed (0 when not limited)."""
        if not self.enabled:
            return 0.0
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                return 0.0
            return round((1.0 - self._tokens) / (self.per_minute / 60.0), 2)

    def check(self, what: str = "action") -> None:
        """Raise :class:`PolicyDenied` when the bucket is empty."""
        if self.allow():
            return
        wait = self.retry_after()
        raise PolicyDenied(
            f"Rate limit reached: too many {what} in a short time. "
            f"Try again in {wait:.0f}s.",
            code="rate_limited",
        )


__all__ = [
    "MAX_PATTERN_LENGTH",
    "PolicyDenied",
    "RateLimiter",
    "describe_command",
    "domain_matches",
    "host_of",
    "matches_any",
    "redact_url",
    "split_patterns",
    "url_allowed",
]
