"""Append-only JSONL audit trail on the node.

The log is written next to the node's other state (``node-data/audit.jsonl``),
readable only by the current user, and rotated once past ``max_bytes`` so a long
running node cannot fill the disk. Every write is best effort: auditing must
never be the reason a command fails or the node crashes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
MAX_TAIL = 500


class AuditLog:
    """JSONL audit log for one node."""

    def __init__(
        self,
        path: Optional[Path],
        *,
        enabled: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.enabled = bool(enabled and self.path is not None)
        self.max_bytes = max(64 * 1024, int(max_bytes))
        self._lock = threading.Lock()

    @classmethod
    def disabled(cls) -> "AuditLog":
        return cls(None, enabled=False)

    # -- writing ---------------------------------------------------------
    def record(self, event: Dict[str, Any], **fields: Any) -> None:
        """Append one event. Never raises."""
        if not self.enabled or self.path is None:
            return
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "source": "node",
        }
        payload.update({key: value for key, value in event.items() if value is not None})
        payload.update({key: value for key, value in fields.items() if value is not None})
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                created = not self.path.exists()
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                if created:
                    try:
                        os.chmod(self.path, 0o600)
                    except OSError:
                        pass
            except OSError as exc:  # pragma: no cover - disk problems
                logger.debug("Could not write the audit log: %s", exc)

    def _rotate_if_needed(self) -> None:
        if self.path is None:
            return
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
        except OSError:
            return
        try:
            os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
        except OSError:  # pragma: no cover - defensive
            pass

    # -- reading ---------------------------------------------------------
    def tail(self, limit: int = 100) -> List[Dict[str, Any]]:
        """The most recent events, oldest first. Never raises."""
        if not self.enabled or self.path is None:
            return []
        limit = max(1, min(MAX_TAIL, int(limit)))
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        events: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                events.append(value)
        # Slice after parsing: a torn or hand-edited line must not swallow the
        # events that follow it.
        return events[-limit:]


__all__ = ["AuditLog", "DEFAULT_MAX_BYTES"]
