"""Optional global hotkey kill switch.

``pynput`` is an optional dependency: when it is missing the node logs a hint
and relies on the control page and ``python -m node.cli stop`` instead.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .safety import SafetyState

logger = logging.getLogger(__name__)

DEFAULT_STOP_COMBO = "<ctrl>+<alt>+<shift>+q"
DEFAULT_PAUSE_COMBO = "<ctrl>+<alt>+<shift>+p"


def start_hotkeys(
    safety: SafetyState,
    *,
    on_stop: Callable[[str], None] | None = None,
    stop_combo: str = DEFAULT_STOP_COMBO,
    pause_combo: str = DEFAULT_PAUSE_COMBO,
) -> Any:
    """Start a global hotkey listener, or return ``None`` if unavailable."""
    try:
        from pynput import keyboard
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.info(
            "Global hotkeys unavailable (%s). Install pynput, use %s, or open the control page.",
            exc,
            "`python -m node.cli stop`",
        )
        return None

    def stop() -> None:
        safety.stop("Stopped with the global hotkey.")
        logger.warning("Kill switch: stopped with the global hotkey.")
        if on_stop is not None:
            on_stop("Stopped with the global hotkey.")

    def toggle_pause() -> None:
        if safety.paused:
            safety.resume()
            logger.info("Resumed with the global hotkey.")
        else:
            safety.pause("Paused with the global hotkey.")
            logger.info("Paused with the global hotkey.")

    try:
        listener = keyboard.GlobalHotKeys({stop_combo: stop, pause_combo: toggle_pause})
        listener.daemon = True
        listener.start()
    except Exception as exc:  # pragma: no cover - depends on the display server
        logger.info("Global hotkeys could not start: %s", exc)
        return None
    logger.info("Hotkeys: %s stops, %s pauses/resumes.", stop_combo, pause_combo)
    return listener


__all__ = ["DEFAULT_PAUSE_COMBO", "DEFAULT_STOP_COMBO", "start_hotkeys"]
