"""Visible "the agent is driving this machine" indicator.

A small always-on-top strip in the corner of the screen, plus a desktop
notification when the state changes and ``notify-send`` is available. It is
deliberately non-interactive: stopping the node still happens through the
control page, the CLI or the hotkey.

The window is drawn with Tk so it works on X11/XWayland without extra
dependencies. When Tk or a display is missing the indicator degrades to
notifications only, and never stops the node from running.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_COLOURS = {
    "connected": ("#0b3d2c", "#7ee2b8"),
    "idle": ("#1b1f26", "#9aa3b2"),
    "paused": ("#3d3208", "#f2d16b"),
    "stopped": ("#3d1114", "#fca5a5"),
}


def indicator_state(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Turn a node status snapshot into text, level and detail."""
    safety = snapshot.get("safety")
    if not isinstance(safety, dict):
        safety = {}

    stopped = bool(snapshot.get("stopped") or safety.get("stopped"))
    paused = bool(snapshot.get("paused") or safety.get("paused"))
    connected = bool(snapshot.get("connected"))
    backend = str(snapshot.get("backend") or "")

    if stopped:
        return {
            "text": "Agent stopped",
            "level": "stopped",
            "detail": "restart the node to allow control",
        }
    if paused:
        detail = str(safety.get("reason") or snapshot.get("reason") or "paused locally")
        return {"text": "Agent paused", "level": "paused", "detail": detail[:80]}
    if connected:
        return {
            "text": "Agent controlling this machine",
            "level": "connected",
            "detail": backend,
        }
    return {
        "text": "Agent idle",
        "level": "idle",
        "detail": "waiting for the server",
    }


class ScreenIndicator:
    """Corner overlay + notifications. All failures are non-fatal."""

    def __init__(
        self,
        status_provider: Callable[[], Dict[str, Any]],
        *,
        enabled: bool = True,
        notify: bool = True,
        poll_seconds: float = 0.75,
    ) -> None:
        self.status_provider = status_provider
        self.enabled = enabled
        self.notify = notify
        self.poll_seconds = max(0.2, float(poll_seconds))
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._last_level = ""
        self._root: Any = None
        self._label: Any = None
        self._frame: Any = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            import tkinter  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on the host
            logger.info("On-screen indicator unavailable (no tkinter: %s).", exc)
            self._notify("Agent control indicator is unavailable (tkinter missing).")
            return False
        self._thread = threading.Thread(
            target=self._mainloop, name="occ-node-indicator", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop = True

    # -- internal --------------------------------------------------------
    def _snapshot(self) -> Dict[str, Any]:
        try:
            value = self.status_provider() or {}
        except Exception:  # pragma: no cover - defensive
            return {}
        return value if isinstance(value, dict) else {}

    def _state(self) -> Dict[str, str]:
        return indicator_state(self._snapshot())

    def _notify(self, message: str) -> None:
        if not self.notify or not shutil.which("notify-send"):
            return
        try:
            subprocess.Popen(
                ["notify-send", "-a", "Open Computer Use", "-t", "4000", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:  # pragma: no cover - optional tool
            pass

    def _on_change(self, state: Dict[str, str]) -> None:
        level = state.get("level") or ""
        if level == self._last_level:
            return
        self._last_level = level
        if level == "connected":
            self._notify("The agent is now controlling this machine.")
        elif level == "paused":
            self._notify("The agent is paused on this machine.")
        elif level == "stopped":
            self._notify("The agent was stopped on this machine.")

    def _mainloop(self) -> None:  # pragma: no cover - needs a display
        try:
            import tkinter as tk
        except Exception:
            return
        try:
            root = tk.Tk()
        except Exception as exc:
            logger.info("On-screen indicator could not open a window: %s", exc)
            return

        try:
            root.overrideredirect(True)
            try:
                root.attributes("-topmost", True)
                root.attributes("-alpha", 0.92)
            except Exception:
                pass
            state = self._state()
            background, foreground = _COLOURS.get(state["level"], _COLOURS["idle"])
            frame = tk.Frame(root, bg=background, highlightthickness=1, highlightbackground=foreground)
            label = tk.Label(
                frame,
                text="",
                bg=background,
                fg=foreground,
                font=("TkDefaultFont", 10),
                padx=12,
                pady=6,
                justify="left",
            )
            label.pack()
            frame.pack()
            root.update_idletasks()
            width = max(240, label.winfo_reqwidth() + 24)
            height = label.winfo_reqheight()
            screen_width = root.winfo_screenwidth()
            root.geometry(f"{width}x{height}+{max(0, screen_width - width - 20)}+20")
            self._root = root
            self._label = label
            self._frame = frame
            self._tick()
            root.mainloop()
        except Exception as exc:  # pragma: no cover - depends on the WM
            logger.info("On-screen indicator stopped unexpectedly: %s", exc)
        finally:
            self._destroy()

    def _destroy(self) -> None:  # pragma: no cover - needs a display
        root, self._root = self._root, None
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    def _tick(self) -> None:  # pragma: no cover - needs a display
        root = self._root
        if root is None:
            return
        if self._stop:
            self._destroy()
            return
        state = self._state()
        self._on_change(state)
        background, foreground = _COLOURS.get(state["level"], _COLOURS["idle"])
        try:
            detail = f" · {state['detail']}" if state.get("detail") else ""
            self._label.configure(
                text=f"{state['text']}{detail}", bg=background, fg=foreground
            )
            self._frame.configure(bg=background, highlightbackground=foreground)
        except Exception:
            pass
        try:
            root.after(int(self.poll_seconds * 1000), self._tick)
        except Exception:
            self._destroy()


__all__ = ["ScreenIndicator", "indicator_state"]
