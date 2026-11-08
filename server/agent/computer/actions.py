"""Tool definitions and execution for computer control."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..providers.base import ToolCall, ToolSpec
from node.drivers import apps as app_catalog
from shared.computer import Computer, ComputerError, EmergencyStop, Frame

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


AppChoiceCallback = Callable[[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]
SaveAppCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]


class ComputerToolbox:
    def __init__(
        self,
        computer: Computer,
        default_apps: Optional[Dict[str, Dict[str, Any]]] = None,
        request_app_choice: Optional[AppChoiceCallback] = None,
        save_default_app: Optional[SaveAppCallback] = None,
        browser_mode: bool = False,
    ) -> None:
        self.computer = computer
        self.default_apps: Dict[str, Dict[str, Any]] = default_apps or {}
        self.request_app_choice = request_app_choice
        self.save_default_app = save_default_app
        self.browser_mode = browser_mode
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
        tools = [
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
                name="open_item",
                description=(
                    "Open a file, folder, installed app or web address. Uses the saved default "
                    "app for that file type; when no default is saved the human is asked to pick "
                    "an app and may choose to always use it. In browser mode it opens the address "
                    "in the current tab and never launches desktop apps."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "File path, folder, app name or web address.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "Optional kind such as '.pdf', 'url' or 'folder'.",
                        },
                    },
                    "required": ["target"],
                },
            ),
            ToolSpec(
                name="list_apps",
                description=(
                    "List installed applications, optionally filtered by a search query, so you "
                    "can see what is available before opening something."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Optional name filter."},
                    },
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
        if self.browser_mode:
            tools.insert(
                0,
                ToolSpec(
                    name="navigate",
                    description=(
                        "Open a web address or search in the browser tab. Use this for every "
                        "website visit in browser mode: screenshots show page content only, so "
                        "there is no address bar to click and ctrl+l or typing a URL will not "
                        "navigate. Pass a full address such as 'https://example.com'; a plain "
                        "query opens search results."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Web address or search query.",
                            },
                        },
                        "required": ["target"],
                    },
                ),
            )
        return tools

    async def _browse_to(self, target: str, force_url: bool = False) -> str:
        """Navigate the browser tab to an address, or search for the text."""
        text = (target or "").strip()
        if not text:
            raise ToolError("target is required (a web address or search query).")
        navigate = getattr(self.computer, "navigate", None)
        if navigate is None:
            raise ToolError(
                "This screen driver cannot open browser tabs; switch the composer toggle to Browser."
            )
        if force_url or app_catalog.is_url(text):
            url = app_catalog.with_scheme(text)
        else:
            url = app_catalog.search_url(text)
        await navigate(url)
        return url

    # -- node-backed apps -------------------------------------------------
    @property
    def _uses_node_apps(self) -> bool:
        """True when the connected node, not this server, owns the app catalog."""
        return callable(getattr(self.computer, "open_item", None))

    async def _node_app_candidates(
        self, query: str = "", kind: str = ""
    ) -> List[Dict[str, str]]:
        """Apps the node admits to having: ``{app_id, name}`` only, no paths."""
        lister = getattr(self.computer, "list_apps", None)
        if lister is None:
            return []
        try:
            data = await lister(query, kind=kind or None)
        except TypeError:  # pragma: no cover - older signature
            data = await lister(query)
        apps = data.get("apps") if isinstance(data, dict) else data
        results: List[Dict[str, str]] = []
        for item in apps or []:
            if not isinstance(item, dict):
                continue
            app_id = str(item.get("app_id") or item.get("id") or "").strip()
            if not app_id:
                continue
            results.append({"app_id": app_id, "name": str(item.get("name") or app_id)})
        return results

    async def _open_on_node(self, app_id: str, name: str, target: str, kind: str) -> str:
        opener = getattr(self.computer, "open_item")
        data = await opener(target, kind=kind or None, app_id=app_id)
        result = data.get("result") if isinstance(data, dict) else None
        return str(result or f"Opened {target} with {name}.")

    async def _open_item_on_node(
        self, target: str, kind_arg: str, args: Dict[str, Any]
    ) -> str:
        if app_catalog.is_url(target):
            data = await self.computer.open_item(target, kind="url")
            result = data.get("result") if isinstance(data, dict) else None
            return str(result or f"Opened {target}.")

        path = Path(target).expanduser()
        if path.exists():
            target = str(path.resolve())
        is_path = path.exists() or target.startswith(("/", "~", ".")) or bool(kind_arg)

        if not is_path:
            matches = await self._node_app_candidates(query=target)
            if not matches:
                raise ToolError(
                    f"No installed app on the node is named '{target}'. Use list_apps to "
                    "see what is available, or pass an existing file path or web address."
                )
            if len(matches) == 1:
                only = matches[0]
                return await self._open_on_node(only["app_id"], only["name"], target, "app")
            return await self._ask_and_open_on_node(
                "app",
                f'apps matching "{target}"',
                target,
                matches,
                can_remember=False,
            )

        kind = kind_arg or app_catalog.kind_for_target(target)
        label = app_catalog.kind_label(kind)
        saved = self.default_apps.get(kind)
        if saved and saved.get("app_id"):
            opened = await self._open_on_node(
                str(saved["app_id"]),
                str(saved.get("app_name") or saved["app_id"]),
                target,
                kind,
            )
            return f"{opened} ({label} default)"

        candidates = await self._node_app_candidates(kind=kind)
        if not candidates:
            candidates = (await self._node_app_candidates())[:80]
        if not candidates:
            raise ToolError(f"The node has no app that can open {target}.")
        return await self._ask_and_open_on_node(
            kind, label, target, candidates, can_remember=kind != "app"
        )

    async def _ask_and_open_on_node(
        self,
        kind: str,
        label: str,
        target: str,
        candidates: List[Dict[str, str]],
        can_remember: bool,
    ) -> str:
        if self.request_app_choice is None:
            first = candidates[0]
            return await self._open_on_node(
                first["app_id"], first["name"], target, kind
            )
        choice = await self.request_app_choice(
            {
                "kind": kind,
                "label": label,
                "target": target,
                "apps": [
                    {
                        "id": item["app_id"],
                        "name": item["name"],
                        "command": "",
                        "categories": "",
                        "mime_types": [],
                    }
                    for item in candidates[:80]
                ],
                "suggested": "",
                "can_remember": can_remember,
            }
        )
        if not choice or not choice.get("app_id"):
            raise ToolError("The human cancelled the app picker, so nothing was opened.")

        app_id = str(choice["app_id"])
        match = next((item for item in candidates if item["app_id"] == app_id), None)
        name = str(choice.get("app_name") or (match["name"] if match else app_id))

        note = ""
        if choice.get("always") and can_remember:
            saved = {
                "kind": kind,
                "label": label,
                "app_id": app_id,
                "app_name": name,
                "command": "",
            }
            self.default_apps[kind] = saved
            if self.save_default_app is not None:
                await self.save_default_app(kind, saved)
            note = f" I will use {name} for {label} from now on."
        return await self._open_on_node(app_id, name, target, kind) + note

    async def _open_item(self, args: Dict[str, Any]) -> str:
        target = str(args.get("target") or "").strip()
        if not target:
            raise ToolError("target is required (a file path, folder, app name or web address).")
        kind_arg = str(args.get("kind") or "").strip().lower()

        if self.browser_mode:
            url = await self._browse_to(target, force_url=kind_arg == "url")
            return f"Opened {url} in the browser tab."

        if self._uses_node_apps:
            return await self._open_item_on_node(target, kind_arg, args)

        if app_catalog.is_url(target):
            kind = "url"
        else:
            path = Path(target).expanduser()
            is_path = path.exists() or target.startswith(("/", "~", "."))
            if path.exists():
                target = str(path.resolve())
            if not is_path and not kind_arg:
                matches = [
                    item
                    for item in app_catalog.installed_apps()
                    if target.lower() in (item.name.lower(), item.id.lower())
                ]
                if not matches:
                    raise ToolError(
                        f"No installed app named '{target}'. Use list_apps to see installed "
                        "apps, or pass an existing file path or web address."
                    )
                if len(matches) == 1:
                    return self._launch(matches[0], None)
                return await self._ask_and_launch(
                    kind="app",
                    label=f'apps matching "{target}"',
                    target=target,
                    candidates=matches,
                    can_remember=False,
                )
            kind = kind_arg or app_catalog.kind_for_target(target)

        label = app_catalog.kind_label(kind)
        saved = self.default_apps.get(kind)
        if saved:
            return self._launch(app_catalog.app_from_saved(saved), target) + f" ({label} default)"

        mime = app_catalog.mime_for_kind(kind)
        candidates = app_catalog.apps_for_mime(mime) if mime else []
        if not candidates:
            candidates = app_catalog.installed_apps()[:80]
        can_remember = kind != "app" and bool(candidates)
        return await self._ask_and_launch(kind, label, target, candidates, can_remember)

    def _launch(self, app: app_catalog.InstalledApp, target: Optional[str]) -> str:
        if self.computer.backend == "simulated":
            label = target or app.name
            return f"Simulated desktop: would open {label} with {app.name}."
        try:
            return app_catalog.launch_app(app, target)
        except Exception as exc:
            raise ToolError(f"Could not launch {app.name}: {exc}") from exc

    async def _ask_and_launch(
        self,
        kind: str,
        label: str,
        target: str,
        candidates: List[app_catalog.InstalledApp],
        can_remember: bool,
    ) -> str:
        suggested = app_catalog.system_default_app_id(kind) if can_remember else ""

        if self.request_app_choice is None:
            fallback = app_catalog.find_app(suggested) if suggested else None
            if fallback is None and candidates:
                fallback = candidates[0]
            if fallback is None:
                raise ToolError(f"No app is available to open {target}.")
            return self._launch(fallback, target)

        choice = await self.request_app_choice(
            {
                "kind": kind,
                "label": label,
                "target": target,
                "apps": [item.to_dict() for item in candidates[:80]],
                "suggested": suggested,
                "can_remember": can_remember,
            }
        )
        if not choice or not choice.get("app_id"):
            raise ToolError("The human cancelled the app picker, so nothing was opened.")

        app = next((item for item in candidates if item.id == choice["app_id"]), None)
        if app is None:
            app = app_catalog.find_app(str(choice["app_id"]))
        if app is None:
            raise ToolError("That app is no longer installed.")

        note = ""
        if choice.get("always") and can_remember:
            saved = {
                "kind": kind,
                "label": label,
                "app_id": app.id,
                "app_name": app.name,
                "command": app.command,
            }
            self.default_apps[kind] = saved
            if self.save_default_app is not None:
                await self.save_default_app(kind, saved)
            note = f" I will use {app.name} for {label} from now on."
        return self._launch(app, target) + note

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

            if name == "navigate":
                if not self.browser_mode:
                    raise ToolError(
                        "navigate only works in Browser mode. Switch the composer toggle to Browser."
                    )
                url = await self._browse_to(str(args.get("target") or ""))
                return f"Opened {url} in the browser tab.", False

            if name == "open_item":
                return await self._open_item(args), False

            if name == "list_apps":
                query = str(args.get("query") or "").strip().lower()
                if self._uses_node_apps:
                    remote_apps = await self._node_app_candidates(query=query)
                    if not remote_apps:
                        return "No matching apps are installed on the node.", False
                    listing = "; ".join(
                        f"{item['name']} [{item['app_id']}]" for item in remote_apps[:25]
                    )
                    return f"Installed apps ({len(remote_apps)}): {listing}", False
                apps = app_catalog.installed_apps()
                if query:
                    apps = [
                        item
                        for item in apps
                        if query in item.name.lower() or query in item.id.lower()
                    ]
                if not apps:
                    return "No matching apps are installed.", False
                listing = "; ".join(f"{item.name} [{item.id}]" for item in apps[:25])
                return f"Installed apps ({len(apps)}): {listing}", False

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


__all__ = ["ComputerToolbox", "ToolError", "TOOL_TIMEOUT", "AppChoiceCallback", "SaveAppCallback"]
