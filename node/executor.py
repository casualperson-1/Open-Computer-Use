"""Runs protocol commands against this machine's drivers."""

from __future__ import annotations

import asyncio
import base64
import logging
import platform
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from shared.protocol import ProtocolError, validate_command

from .config import NodeConfig, session_info
from .drivers import apps as app_catalog
from .drivers import create_driver
from .safety import SafetyState

logger = logging.getLogger(__name__)

# Commands that are allowed while the node is paused, and that keep working
# after it is stopped so the kill switch can always be reached remotely.
_ALWAYS_ALLOWED = {"describe", "pause", "resume", "stop"}


class CommandExecutor:
    """Owns the local driver and maps protocol commands onto it."""

    def __init__(
        self,
        config: NodeConfig,
        safety: SafetyState,
        on_stop: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.safety = safety
        self.on_stop = on_stop
        self._driver = None
        self._create_lock = asyncio.Lock()

    # -- driver ----------------------------------------------------------
    async def driver(self):
        if self._driver is None:
            async with self._create_lock:
                if self._driver is None:
                    self._driver = await asyncio.to_thread(
                        create_driver,
                        self.config.backend,
                        max_width=self.config.max_width,
                        human_mouse=self.config.human_mouse,
                        user_data_dir=str(self.config.profile_dir())
                        if self.config.backend == "browser"
                        else None,
                        headless=self.config.headless,
                    )
                    logger.info("Driver ready: %s", self._driver.backend)
        return self._driver

    async def close(self) -> None:
        driver, self._driver = self._driver, None
        closer = getattr(driver, "close", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Could not close the driver")

    async def status(self) -> Dict[str, Any]:
        """Describe this node for the handshake and the control page."""
        info: Dict[str, Any] = {
            "name": self.config.label,
            "backend": self.config.backend,
            "platform": platform.platform(),
            "session": session_info(),
            "safety": self.safety.snapshot(),
            "capabilities": [],
            "screen": {},
        }
        try:
            driver = await self.driver()
            info["capabilities"] = list(_CAPABILITIES)
            described = driver.describe()
            info["screen"] = {
                "width": described.get("screen_width"),
                "height": described.get("screen_height"),
                "label": described.get("label") or described.get("name") or "",
            }
        except Exception as exc:  # pragma: no cover - depends on the host
            info["error"] = str(exc)
        return info

    # -- dispatch --------------------------------------------------------
    async def execute(self, name: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        clean = validate_command(name, params)
        if name not in _ALWAYS_ALLOWED:
            self.safety.check()
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:  # pragma: no cover - validate_command already guards this
            raise ProtocolError(f"Unsupported command '{name}'.", code="unsupported_command")
        return await handler(clean)

    # -- pointer and keyboard -------------------------------------------
    async def _cmd_mouse_move(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        await driver.move(params["x"], params["y"])
        return {"x": params["x"], "y": params["y"]}

    async def _cmd_click(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        await driver.click(
            params["x"],
            params["y"],
            button=params.get("button", "left"),
            clicks=int(params.get("clicks", 1)),
        )
        return {
            "x": params["x"],
            "y": params["y"],
            "button": params.get("button", "left"),
            "clicks": int(params.get("clicks", 1)),
        }

    async def _cmd_drag(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        await driver.drag(
            params["from_x"], params["from_y"], params["to_x"], params["to_y"]
        )
        return {"from": [params["from_x"], params["from_y"]], "to": [params["to_x"], params["to_y"]]}

    async def _cmd_scroll(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        x, y = params.get("x"), params.get("y")
        if x is None or y is None:
            described = driver.describe()
            x = float(described.get("screen_width") or 0) / 2
            y = float(described.get("screen_height") or 0) / 2
        amount = int(params.get("amount", 480))
        await driver.scroll(float(x), float(y), amount)
        return {"x": x, "y": y, "amount": amount}

    async def _cmd_type(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        await driver.type_text(params["text"])
        return {"characters": len(params["text"])}

    async def _cmd_key_combo(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        await driver.press_key(params["keys"])
        return {"keys": params["keys"]}

    # -- observation -----------------------------------------------------
    async def _cmd_screenshot(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        frame = await driver.capture()
        return {
            "png_base64": base64.b64encode(frame.png).decode("ascii"),
            "width": frame.width,
            "height": frame.height,
            "screen_width": frame.screen_width,
            "screen_height": frame.screen_height,
            "cursor": [frame.cursor_x, frame.cursor_y],
            "label": frame.label,
        }

    # -- browser ---------------------------------------------------------
    async def _cmd_open_url(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        navigate = getattr(driver, "navigate", None)
        if navigate is not None:
            await navigate(params["url"])
            return {"url": params["url"], "via": "browser"}
        opened = await asyncio.to_thread(webbrowser.open, params["url"])
        return {"url": params["url"], "via": "default-browser", "opened": bool(opened)}

    async def _cmd_browser_action(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        perform = getattr(driver, "perform", None)
        if perform is None:
            raise ProtocolError(
                "This node is not running the browser backend.", code="wrong_backend"
            )
        args = {key: value for key, value in params.items() if key != "action"}
        return await perform(params["action"], **args)

    async def _cmd_get_page_text(self, params: Dict[str, Any]) -> Dict[str, Any]:
        driver = await self.driver()
        reader = getattr(driver, "page_text", None)
        if reader is None:
            raise ProtocolError(
                "This node is not running the browser backend.", code="wrong_backend"
            )
        text = await reader(
            selector=params.get("selector"), max_chars=int(params.get("max_chars", 20_000))
        )
        return {"text": text, "characters": len(text)}

    # -- apps ------------------------------------------------------------
    async def _cmd_list_apps(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = (params.get("query") or "").strip().lower()
        installed = await asyncio.to_thread(app_catalog.installed_apps)
        if query:
            installed = [
                item
                for item in installed
                if query in item.name.lower() or query in item.id.lower()
            ]
        return {
            "apps": [item.to_dict() for item in installed[:80]],
            "count": len(installed),
        }

    async def _cmd_open_item(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target = params["target"].strip()
        kind = (params.get("kind") or "").strip().lower()

        if app_catalog.is_url(target) or kind == "url":
            url = app_catalog.with_scheme(target)
            result = await self._cmd_open_url({"url": url})
            return {**result, "target": target}

        path = Path(target).expanduser()
        if path.exists():
            resolved = str(path.resolve())
            mime = app_catalog.mime_for_kind(app_catalog.kind_for_target(resolved))
            candidates = await asyncio.to_thread(app_catalog.apps_for_mime, mime)
            if not candidates:
                raise ProtocolError(
                    f"No installed app handles {mime or 'this file'}.", code="no_app"
                )
            summary = await asyncio.to_thread(app_catalog.launch_app, candidates[0], resolved)
            return {"target": resolved, "app": candidates[0].name, "result": summary}

        app = await asyncio.to_thread(app_catalog.find_app, target)
        if app is None:
            raise ProtocolError(
                f"No installed app or file named '{target}'.", code="not_found"
            )
        summary = await asyncio.to_thread(app_catalog.launch_app, app, None)
        return {"target": target, "app": app.name, "result": summary}

    # -- node control ----------------------------------------------------
    async def _cmd_describe(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        return await self.status()

    async def _cmd_pause(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self.safety.pause(params.get("reason") or "Paused by the server.")
        return self.safety.snapshot()

    async def _cmd_resume(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        self.safety.resume()
        return self.safety.snapshot()

    async def _cmd_stop(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reason = params.get("reason") or "Stopped by the server."
        self.safety.stop(reason)
        if self.on_stop is not None:
            self.on_stop(reason)
        return self.safety.snapshot()


_CAPABILITIES = (
    "mouse_move",
    "click",
    "drag",
    "scroll",
    "type",
    "key_combo",
    "screenshot",
    "open_url",
    "browser_action",
    "get_page_text",
    "list_apps",
    "open_item",
    "describe",
)


__all__ = ["CommandExecutor"]
