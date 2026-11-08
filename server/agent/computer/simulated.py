"""In-memory virtual desktop used for dry runs, demos and headless tests."""

from __future__ import annotations

import asyncio
import itertools
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

from shared.computer import Computer, Frame


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


class SimulatedComputer(Computer):
    """A self-contained desktop rendered with Pillow.

    It behaves like the real driver (coordinates are screen pixels) but no host
    input is generated, which makes it safe for demos and CI.
    """

    backend = "simulated"
    supports_real_input = False

    def __init__(self, width: int = 1280, height: int = 720, max_width: int = 1280) -> None:
        self.real_width = width
        self.real_height = height
        self.max_width = max_width
        self.cursor: Tuple[float, float] = (width * 0.6, height * 0.55)
        self.notes: str = ""
        self.selection: str = "Documents"
        self.last_action: str = "Desktop ready"
        self.events: List[Dict[str, Any]] = []
        self.started_at = time.time()
        self._ids = itertools.count(1)

    async def capture(self) -> Frame:
        await asyncio.sleep(0)
        image = self._render()
        image = self._scaled(image, self.max_width)
        return Frame(
            png=self._png_bytes(image),
            width=image.width,
            height=image.height,
            screen_width=self.real_width,
            screen_height=self.real_height,
            cursor_x=self.cursor[0],
            cursor_y=self.cursor[1],
            label="Open Computer Use demo desktop",
        )

    def _record(self, action: str, **payload: Any) -> None:
        self.events.append(
            {
                "id": next(self._ids),
                "action": action,
                "at": round(time.time() - self.started_at, 3),
                **payload,
            }
        )
        self.events = self.events[-200:]
        self.last_action = payload.get("description", action)
        self.notes = self.notes[-600:]

    async def move(self, x: float, y: float) -> None:
        x = max(0, min(self.real_width - 1, x))
        y = max(0, min(self.real_height - 1, y))
        self.cursor = (x, y)
        self._record("move", x=round(x), y=round(y), description=f"Move cursor to {x:.0f},{y:.0f}")

    async def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        await self.move(x, y)
        self._record(
            "click",
            x=round(x),
            y=round(y),
            button=button,
            clicks=clicks,
            description=f"{button} click x{clicks} at {x:.0f},{y:.0f}",
        )

    async def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        await self.move(to_x, to_y)
        self._record(
            "drag",
            from_xy=[round(from_x), round(from_y)],
            to_xy=[round(to_x), round(to_y)],
            description=f"Drag {from_x:.0f},{from_y:.0f} to {to_x:.0f},{to_y:.0f}",
        )

    async def scroll(self, x: float, y: float, amount: int) -> None:
        await self.move(x, y)
        self._record("scroll", amount=amount, description=f"Scroll {amount} at {x:.0f},{y:.0f}")

    async def type_text(self, text: str) -> None:
        self.notes += text
        self._record("type", text=text, description=f"Typed {len(text)} characters")

    async def press_key(self, keys: str) -> None:
        self._record("key", keys=keys, description=f"Pressed {keys}")
        if keys.lower() in ("enter", "return") and not self.notes.endswith("\n"):
            self.notes += "\n"

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self) -> Image.Image:
        width, height = self.real_width, self.real_height
        image = Image.new("RGB", (width, height), "#0b1220")
        draw = ImageDraw.Draw(image)

        # Wallpaper accent
        for y in range(0, height, 4):
            shade = 12 + int(18 * (y / max(1, height)))
            draw.line([(0, y), (width, y)], fill=(shade, 20 + shade // 3, 34 + shade))

        small = _font(14)
        body = _font(16)
        mono = _font(15)

        # Top bar
        draw.rectangle([0, 0, width, 34], fill="#111a2e")
        draw.text((18, 9), "Open Computer Use  ·  simulated desktop", font=body, fill="#c7d2fe")
        clock = datetime.now().strftime("%a %H:%M")
        draw.text((width - 150, 10), clock, font=small, fill="#94a3b8")
        draw.ellipse([width - 28, 12, width - 18, 22], fill="#22c55e")

        # Files window
        draw.rounded_rectangle([44, 70, 430, 470], radius=12, fill="#1e293b", outline="#334155", width=1)
        draw.rounded_rectangle([44, 70, 430, 108], radius=12, fill="#273449")
        draw.text((64, 82), "Files — Home", font=body, fill="#e2e8f0")
        for index, (dot, label) in enumerate(
            [("#f87171", "Documents"), ("#fbbf24", "Downloads"), ("#34d399", "Projects"), ("#60a5fa", "Pictures")]
        ):
            y = 128 + index * 44
            if label == self.selection:
                draw.rounded_rectangle([58, y - 6, 416, y + 28], radius=7, fill="#334155")
            draw.ellipse([70, y, 88, y + 18], fill=dot)
            draw.text((102, y - 2), label, font=body, fill="#e2e8f0")
        draw.text((64, 330), "Local disk · 128 GB free", font=small, fill="#94a3b8")
        draw.rectangle([64, 360, 404, 366], fill="#0f172a")
        draw.rectangle([64, 360, 268, 366], fill="#6366f1")

        # Notes window
        draw.rounded_rectangle([470, 70, 1030, 560], radius=12, fill="#111827", outline="#334155", width=1)
        draw.rounded_rectangle([470, 70, 1030, 108], radius=12, fill="#1f2937")
        draw.text((490, 82), "Notes — agent transcript", font=body, fill="#e2e8f0")
        text = self.notes.strip() or "Anything the agent types appears here."
        y = 128
        for line in text.splitlines()[-18:]:
            draw.text((492, y), line[:95], font=mono, fill="#cbd5e1")
            y += 24

        # Status / action panel
        draw.rounded_rectangle([44, 500, 430, 690], radius=12, fill="#0f172a", outline="#334155")
        draw.text((62, 516), "LAST ACTION", font=small, fill="#818cf8")
        action = self.last_action
        draw.text((62, 540), action[:42], font=body, fill="#f8fafc")
        draw.text((62, 576), "RECENT EVENTS", font=small, fill="#818cf8")
        for index, event in enumerate(reversed(self.events[-4:])):
            label = f"{event['action']}: {event.get('description', '')}"
            draw.text((62, 600 + index * 20), label[:60], font=small, fill="#94a3b8")

        # Bottom dock
        draw.rounded_rectangle([470, 600, 1030, 680], radius=14, fill="#111a2e", outline="#334155")
        for index, (colour, name) in enumerate(
            [("#ef4444", "Browser"), ("#3b82f6", "Files"), ("#22c55e", "Terminal"), ("#a855f7", "Notes")]
        ):
            x = 502 + index * 130
            draw.rounded_rectangle([x, 620, x + 56, 660], radius=10, fill=colour)
            draw.text((x + 6, 668), name, font=small, fill="#94a3b8")

        # Cursor
        cx, cy = self.cursor
        draw.polygon(
            [(cx, cy), (cx, cy + 22), (cx + 6, cy + 16), (cx + 12, cy + 27), (cx + 18, cy + 24), (cx + 11, cy + 13), (cx + 20, cy + 12)],
            fill="#f8fafc",
            outline="#0f172a",
        )

        draw.text(
            (width - 210, height - 30),
            f"cursor {cx:.0f},{cy:.0f}",
            font=small,
            fill="#64748b",
        )
        return image

    def describe(self) -> Dict[str, object]:
        info = super().describe()
        info.update(
            {
                "name": "Simulated desktop",
                "screen_width": self.real_width,
                "screen_height": self.real_height,
                "note": "No host input is generated. Use this for demos and tests.",
            }
        )
        return info


__all__ = ["SimulatedComputer"]
