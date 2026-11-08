"""Computer abstraction shared by the real-desktop and simulated drivers."""

from __future__ import annotations

import base64
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from PIL import Image


class ComputerError(RuntimeError):
    """Raised when an input action cannot be performed."""


class EmergencyStop(ComputerError):
    """Raised when the user triggers the mouse-corner failsafe."""


@dataclass
class Frame:
    """A screenshot plus the mapping back to real screen coordinates."""

    png: bytes
    width: int  # screenshot size the model sees
    height: int
    screen_width: int
    screen_height: int
    cursor_x: float = 0.0
    cursor_y: float = 0.0
    label: str = ""

    @property
    def scale_x(self) -> float:
        return self.screen_width / self.width if self.width else 1.0

    @property
    def scale_y(self) -> float:
        return self.screen_height / self.height if self.height else 1.0

    def jpeg_data_url(self, quality: int = 72, max_width: int = 1280) -> str:
        try:
            image = Image.open(io.BytesIO(self.png)).convert("RGB")
            if image.width > max_width:
                ratio = max_width / image.width
                image = image.resize(
                    (max_width, max(1, int(image.height * ratio))), Image.LANCZOS
                )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
        except Exception:
            encoded = base64.b64encode(self.png).decode("ascii")
            return f"data:image/png;base64,{encoded}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "cursor": [round(self.cursor_x), round(self.cursor_y)],
            "label": self.label,
        }


class Computer(ABC):
    """Minimal desktop surface the agent can observe and drive."""

    backend = "base"
    supports_real_input = False

    @abstractmethod
    async def capture(self) -> Frame:
        ...

    @abstractmethod
    async def click(self, x: float, y: float, button: str = "left", clicks: int = 1) -> None:
        ...

    @abstractmethod
    async def move(self, x: float, y: float) -> None:
        ...

    @abstractmethod
    async def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        ...

    @abstractmethod
    async def scroll(self, x: float, y: float, amount: int) -> None:
        ...

    @abstractmethod
    async def type_text(self, text: str) -> None:
        ...

    @abstractmethod
    async def press_key(self, keys: str) -> None:
        ...

    def describe(self) -> Dict[str, object]:
        return {
            "backend": self.backend,
            "real_input": self.supports_real_input,
        }

    @staticmethod
    def _scaled(image: Image.Image, max_width: int) -> Image.Image:
        if max_width and image.width > max_width:
            ratio = max_width / image.width
            return image.resize(
                (max_width, max(1, int(image.height * ratio))), Image.LANCZOS
            )
        return image

    @staticmethod
    def _png_bytes(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


__all__ = [
    "Computer",
    "ComputerError",
    "EmergencyStop",
    "Frame",
    "Tuple",
    "Optional",
]
