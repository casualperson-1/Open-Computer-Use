"""Real mouse/keyboard/screen driver built on PyAutoGUI + mss.

Works on Windows, macOS and Linux (X11 or XWayland). On Linux a reachable
``DISPLAY`` is required; that can be the user's session or the container's Xvfb.
"""

from __future__ import annotations

import asyncio
import os
import platform
import random
import sys
import time
from typing import Any, List, Optional, Tuple

from PIL import Image

from shared.bezier import generate_bezier_path
from shared.computer import Computer, ComputerError, EmergencyStop, Frame

_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "cmd": "command",
    "command": "command",
    "meta": "win",
    "super": "win",
    "win": "win",
    "windows": "win",
}

_KEY_ALIASES = {
    "esc": "escape",
    "escape": "escape",
    "return": "enter",
    "enter": "enter",
    "del": "delete",
    "delete": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "space": "space",
    "tab": "tab",
    "backspace": "backspace",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
}


class DesktopComputer(Computer):
    backend = "desktop"
    supports_real_input = True

    def __init__(
        self,
        max_width: int = 1280,
        human_mouse: bool = True,
        move_duration: float = 0.32,
    ) -> None:
        self.max_width = max_width
        self.human_mouse = human_mouse
        self.move_duration = move_duration
        self._pyautogui: Any = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    @staticmethod
    def available() -> Tuple[bool, str]:
        if platform.system() == "Linux" and not os.environ.get("DISPLAY"):
            return False, "No X11 DISPLAY found. Log in to a graphical session or use the virtual desktop container."
        try:
            import mss  # noqa: F401
            import pyautogui  # noqa: F401
        except (Exception, SystemExit) as exc:  # pragma: no cover - depends on host packages
            # pyautogui's mouseinfo import calls sys.exit() when tkinter is missing.
            return False, f"pyautogui/mss unavailable: {exc}"
        try:
            size = pyautogui.size()
            if not size or size.width < 10:
                return False, "Could not read the screen size."
        except Exception as exc:  # pragma: no cover
            return False, f"Could not access the display: {exc}"
        if platform.system() == "Darwin":
            return True, "macOS: allow Accessibility + Screen Recording for your terminal/Python in System Settings."
        return True, "Desktop control is available."

    def _ensure(self) -> Any:
        if not self._loaded:
            try:
                import pyautogui

                pyautogui.FAILSAFE = True
                pyautogui.PAUSE = 0
            except (Exception, SystemExit) as exc:
                raise ComputerError(f"Could not initialise pyautogui: {exc}") from exc
            self._pyautogui = pyautogui
            self._loaded = True
        return self._pyautogui

    async def _run(self, func, *args, **kwargs):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            if exc.__class__.__name__ == "FailSafeException":
                raise EmergencyStop(
                    "Emergency stop: the cursor was moved to a screen corner. "
                    "Move the mouse away and start a new run."
                ) from exc
            raise ComputerError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------
    def _capture_sync(self) -> Frame:
        import mss

        pyautogui = self._ensure()
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        screen_width, screen_height = image.width, image.height
        image = self._scaled(image, self.max_width)
        try:
            cursor = pyautogui.position()
            cursor_x, cursor_y = float(cursor.x), float(cursor.y)
        except Exception:
            cursor_x = cursor_y = 0.0

        return Frame(
            png=self._png_bytes(image),
            width=image.width,
            height=image.height,
            screen_width=screen_width,
            screen_height=screen_height,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            label=platform.platform(),
        )

    async def capture(self) -> Frame:
        return await self._run(self._capture_sync)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def _move_sync(self, x: float, y: float) -> None:
        pyautogui = self._ensure()
        if not self.human_mouse:
            pyautogui.moveTo(x, y)
            return

        start = pyautogui.position()
        start_point = (float(start.x), float(start.y))
        target = (float(x), float(y))
        distance = ((target[0] - start_point[0]) ** 2 + (target[1] - start_point[1]) ** 2) ** 0.5
        if distance < 6:
            pyautogui.moveTo(x, y)
            return

        duration = min(0.8, max(0.18, self.move_duration + random.uniform(-0.04, 0.06)))
        steps = max(8, int(duration / 0.012))
        path = generate_bezier_path(start_point, target, steps=steps)
        interval = duration / max(1, len(path))
        for px, py in path:
            pyautogui.moveTo(px, py)
            time.sleep(interval)

    async def move(self, x: float, y: float) -> None:
        await self._run(self._move_sync, x, y)

    def _click_sync(self, x: float, y: float, button: str, clicks: int) -> None:
        pyautogui = self._ensure()
        self._move_sync(x, y)
        pyautogui.click(x=x, y=y, clicks=clicks, interval=0.11, button=button)

    async def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        if button not in ("left", "right", "middle"):
            raise ComputerError(f"Unsupported mouse button '{button}'.")
        await self._run(self._click_sync, x, y, button, max(1, min(3, int(clicks))))

    def _drag_sync(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        pyautogui = self._ensure()
        self._move_sync(from_x, from_y)
        pyautogui.mouseDown()
        time.sleep(0.08)
        start = (from_x, from_y)
        path = generate_bezier_path(start, (to_x, to_y), steps=26)
        for px, py in path:
            pyautogui.moveTo(px, py)
            time.sleep(0.012)
        time.sleep(0.08)
        pyautogui.mouseUp()

    async def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        await self._run(self._drag_sync, from_x, from_y, to_x, to_y)

    def _scroll_sync(self, x: float, y: float, amount: int) -> None:
        pyautogui = self._ensure()
        self._move_sync(x, y)
        clicks = max(1, abs(int(amount)) // 120)
        direction = -1 if amount > 0 else 1  # pyautogui: positive scrolls up
        for _ in range(clicks):
            pyautogui.scroll(direction * 120)
            time.sleep(0.02)

    async def scroll(self, x: float, y: float, amount: int) -> None:
        await self._run(self._scroll_sync, x, y, int(amount))

    def _type_sync(self, text: str) -> None:
        pyautogui = self._ensure()
        if text.isascii():
            lines = text.split("\n")
            for index, line in enumerate(lines):
                if line:
                    pyautogui.write(line, interval=0.008)
                if index < len(lines) - 1:
                    pyautogui.press("enter")
            return

        try:
            import pyperclip

            previous: Optional[str] = None
            try:
                previous = pyperclip.paste()
            except Exception:
                previous = None
            pyperclip.copy(text)
            modifier = "command" if sys.platform == "darwin" else "ctrl"
            pyautogui.hotkey(modifier, "v")
            time.sleep(0.15)
            if previous is not None:
                try:
                    pyperclip.copy(previous)
                except Exception:
                    pass
        except Exception:
            pyautogui.write(text.encode("ascii", "ignore").decode("ascii"), interval=0.008)

    async def type_text(self, text: str) -> None:
        await self._run(self._type_sync, text)

    def _normalise_keys(self, keys: str) -> List[str]:
        parts = [part.strip().lower() for part in keys.split("+") if part.strip()]
        normalised: List[str] = []
        for part in parts:
            if part in _MODIFIER_ALIASES:
                normalised.append(_MODIFIER_ALIASES[part])
            else:
                normalised.append(_KEY_ALIASES.get(part, part))
        return normalised

    def _press_sync(self, keys: str) -> None:
        pyautogui = self._ensure()
        parts = self._normalise_keys(keys)
        if not parts:
            raise ComputerError("No keys supplied.")
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)

    async def press_key(self, keys: str) -> None:
        await self._run(self._press_sync, keys)

    # ------------------------------------------------------------------
    def describe(self) -> dict:
        info = super().describe()
        info.update(
            {
                "name": "Real desktop",
                "platform": platform.platform(),
                "screen_width": None,
                "screen_height": None,
                "note": "Controls this computer's mouse, keyboard and screen. "
                "Move the cursor into a screen corner for an emergency stop.",
            }
        )
        try:
            pyautogui = self._ensure()
            size = pyautogui.size()
            info["screen_width"] = size.width
            info["screen_height"] = size.height
        except Exception:
            pass
        return info


__all__ = ["DesktopComputer"]
