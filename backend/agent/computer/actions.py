"""Tool definitions and execution for computer control."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from ..providers.base import ToolCall, ToolSpec
from .base import Computer, ComputerError, EmergencyStop, Frame

TOOL_TIMEOUT = 30.0
MAX_TYPE_LENGTH = 4000


class ToolError(RuntimeError):
    pass


def _point(
    call: ToolCall, frame: Frame, prefix: str = ""
) -> Tuple[float, float, float, float]:
    """Map screenshot coordinates to real screen coordinates."""
    args = call.arguments
    raw_x = args.get(f"{prefix}x", args.get("x") if not prefix else None)
    raw_y = args.get(f"{prefix}y", args.get("y") if not prefix else None)
    if raw_x is None or raw_y is None:
        raise ToolError(f"'{call.name}' needs numeric x and y coordinates.")
    try:
        x = float(raw_x)
        y = float(raw_y)
    except (TypeError, ValueError) as exc:
        raise ToolError("x and y must be numbers.") from exc

    if x < 0 or y < 0 or x > frame.width or y > frame.height:
        raise ToolError(
            f"({x:.0f}, {y:.0f}) is outside the screenshot. "
            f"Use coordinates between 0 and {frame.width} horizontally and "
            f"0 and {frame.height} vertically."
        )
    return x * frame.scale_x, y * frame.scale_y, x, y


class ComputerToolbox:
    def __init__(self, computer: Computer) -> None:
        self.computer = computer
        self.tools = self._build_tools()

    def _build_tools(self) -> List[ToolSpec]:
        point_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X pixel in the screenshot."},
                "y": {"type": "number", "description": "Y pixel in the screenshot."},
            },
            "required": ["x", "y"],
        }
        return [
            ToolSpec(
                name="click",
                description=(
                    "Move the mouse to (x, y) and click. Coordinates are pixels in the "
                    "attached screenshot, origin at the top-left."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X pixel in the screenshot."},
                        "y": {"type": "number", "description": "Y pixel in the screenshot."},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "description": "Mouse button (default left).",
                        },
                        "clicks": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 3,
                            "description": "1 for a click, 2 for a double click.",
                        },
                    },
                    "required": ["x", "y"],
                },
            ),
            ToolSpec(
                name="double_click",
                description="Double-click at (x, y) in the screenshot. Opens files and folders.",
                parameters=point_schema,
            ),
            ToolSpec(
                name="right_click",
                description="Right-click at (x, y) in the screenshot to open a context menu.",
                parameters=point_schema,
            ),
            ToolSpec(
                name="move_mouse",
                description="Move the cursor to (x, y) without clicking, e.g. to reveal hover menus.",
                parameters=point_schema,
            ),
            ToolSpec(
                name="drag",
                description=(
                    "Press the left mouse button at (from_x, from_y), drag to (to_x, to_y) "
                    "and release, e.g. to move files or select text."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "from_x": {"type": "number", "description": "Start X pixel."},
                        "from_y": {"type": "number", "description": "Start Y pixel."},
                        "to_x": {"type": "number", "description": "End X pixel."},
                        "to_y": {"type": "number", "description": "End Y pixel."},
                    },
                    "required": ["from_x", "from_y", "to_x", "to_y"],
                },
            ),
            ToolSpec(
                name="scroll",
                description="Scroll the window under the cursor. Positive amount scrolls down.",
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "description": "X pixel to scroll over."},
                        "y": {"type": "number", "description": "Y pixel to scroll over."},
                        "amount": {
                            "type": "integer",
                            "description": "Pixels to scroll. Positive = down, negative = up. Default 480.",
                        },
                    },
                    "required": ["amount"],
                },
            ),
            ToolSpec(
                name="type_text",
                description=(
                    "Type text into the currently focused field or window. Click the field "
                    "first. Supports newlines."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to type."},
                    },
                    "required": ["text"],
                },
            ),
            ToolSpec(
                name="press_key",
                description=(
                    "Press a key or keyboard shortcut. Use '+' for combinations, e.g. "
                    "'enter', 'escape', 'tab', 'ctrl+s', 'ctrl+shift+t', 'alt+f4', 'win'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "keys": {"type": "string", "description": "Key or shortcut, e.g. 'ctrl+c'."},
                    },
                    "required": ["keys"],
                },
            ),
            ToolSpec(
                name="wait",
                description="Wait for the screen to update before the next screenshot.",
                parameters={
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "number",
                            "minimum": 0.2,
                            "maximum": 15,
                            "description": "Seconds to wait.",
                        }
                    },
                    "required": ["seconds"],
                },
            ),
            ToolSpec(
                name="ask_user",
                description=(
                    "Pause and ask the human for help, credentials, confirmation of a risky "
                    "action, or visual judgement. Use this instead of guessing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Short question for the user."},
                    },
                    "required": ["question"],
                },
            ),
            ToolSpec(
                name="done",
                description="Finish the task. Call this when the goal is complete or cannot proceed.",
                parameters={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "What was accomplished, in 1-3 sentences.",
                        },
                        "success": {
                            "type": "boolean",
                            "description": "False if the task could not be completed.",
                        },
                    },
                    "required": ["summary"],
                },
            ),
        ]

    async def capture(self) -> Frame:
        return await self.computer.capture()

    async def execute(self, call: ToolCall, frame: Frame) -> Tuple[str, bool]:
        """Execute a tool call. Returns (observation text, is_error)."""
        name = call.name
        args: Dict[str, Any] = call.arguments or {}

        try:
            if name == "click":
                real_x, real_y, x, y = _point(call, frame)
                button = str(args.get("button") or "left").lower()
                clicks = int(args.get("clicks") or 1)
                if button not in ("left", "right", "middle"):
                    raise ToolError("button must be left, right or middle.")
                await asyncio.wait_for(
                    self.computer.click(real_x, real_y, button=button, clicks=clicks),
                    timeout=TOOL_TIMEOUT,
                )
                return f"Clicked {button} x{clicks} at ({x:.0f}, {y:.0f}).", False

            if name == "double_click":
                real_x, real_y, x, y = _point(call, frame)
                await asyncio.wait_for(
                    self.computer.click(real_x, real_y, button="left", clicks=2),
                    timeout=TOOL_TIMEOUT,
                )
                return f"Double-clicked at ({x:.0f}, {y:.0f}).", False

            if name == "right_click":
                real_x, real_y, x, y = _point(call, frame)
                await asyncio.wait_for(
                    self.computer.click(real_x, real_y, button="right", clicks=1),
                    timeout=TOOL_TIMEOUT,
                )
                return f"Right-clicked at ({x:.0f}, {y:.0f}).", False

            if name == "move_mouse":
                real_x, real_y, x, y = _point(call, frame)
                await asyncio.wait_for(self.computer.move(real_x, real_y), timeout=TOOL_TIMEOUT)
                return f"Moved the cursor to ({x:.0f}, {y:.0f}).", False

            if name == "drag":
                from_x, from_y, sx, sy = _point(call, frame, prefix="from_")
                to_x, to_y, tx, ty = _point(call, frame, prefix="to_")
                await asyncio.wait_for(
                    self.computer.drag(from_x, from_y, to_x, to_y), timeout=TOOL_TIMEOUT
                )
                return f"Dragged from ({sx:.0f}, {sy:.0f}) to ({tx:.0f}, {ty:.0f}).", False

            if name == "scroll":
                amount = int(args.get("amount", 480))
                if "x" in args and "y" in args:
                    real_x, real_y, x, y = _point(call, frame)
                else:
                    real_x, real_y = frame.screen_width / 2, frame.screen_height / 2
                    x, y = frame.width / 2, frame.height / 2
                await asyncio.wait_for(
                    self.computer.scroll(real_x, real_y, amount), timeout=TOOL_TIMEOUT
                )
                direction = "down" if amount >= 0 else "up"
                return f"Scrolled {direction} by {abs(amount)} at ({x:.0f}, {y:.0f}).", False

            if name == "type_text":
                text = str(args.get("text") or "")
                if not text:
                    raise ToolError("text is required.")
                if len(text) > MAX_TYPE_LENGTH:
                    text = text[:MAX_TYPE_LENGTH]
                await asyncio.wait_for(self.computer.type_text(text), timeout=TOOL_TIMEOUT)
                return f"Typed {len(text)} characters.", False

            if name == "press_key":
                keys = str(args.get("keys") or "").strip()
                if not keys:
                    raise ToolError("keys is required.")
                await asyncio.wait_for(self.computer.press_key(keys), timeout=TOOL_TIMEOUT)
                return f"Pressed {keys}.", False

            if name == "wait":
                seconds = max(0.2, min(15.0, float(args.get("seconds", 1))))
                await asyncio.sleep(seconds)
                return f"Waited {seconds:.1f}s.", False

            if name == "ask_user":
                question = str(args.get("question") or "Please help with the next step.")
                return question, False

            if name == "done":
                return str(args.get("summary") or "Task finished."), False

            raise ToolError(f"Unknown tool '{name}'.")

        except asyncio.TimeoutError:
            return f"'{name}' timed out after {TOOL_TIMEOUT:.0f}s.", True
        except (ToolError, ComputerError, EmergencyStop) as exc:
            return str(exc), True
        except (TypeError, ValueError) as exc:
            return f"Invalid arguments for '{name}': {exc}", True
        except Exception as exc:  # pragma: no cover - defensive
            return f"'{name}' failed: {exc}", True


__all__ = ["ComputerToolbox", "ToolError", "TOOL_TIMEOUT"]
