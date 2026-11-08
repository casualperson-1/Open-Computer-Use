import asyncio
import ipaddress
import urllib.parse
from typing import Optional, Dict, Any
from ..browser.session import BrowserSession
from ..perception.extractor import PerceptionEngine
from .schema import Action

class SecurityError(Exception):
    pass

class ActionResolver:
    def __init__(self, session: BrowserSession, perception: PerceptionEngine):
        self.session = session
        self.perception = perception

    def validate_url(self, url: str):
        """Block internal IPs, loopback, private networks, and cloud metadata (SSRF defense)."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https", "file"):
            raise SecurityError(f"Prohibited URL scheme: {parsed.scheme}")

        hostname = parsed.hostname
        if not hostname:
            return

        if hostname in ("localhost", "127.0.0.1", "169.254.169.254", "metadata.google.internal"):
            raise SecurityError(f"Access to sensitive host '{hostname}' is blocked.")

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise SecurityError(f"Access to private IP '{hostname}' is blocked.")
        except ValueError:
            # Domain name, allowed
            pass

    async def execute(self, action: Action) -> Dict[str, Any]:
        """Execute action, resolving element coordinates dynamically and retrying once if DOM shifted."""
        name = action.name.lower()
        page = self.session.page
        mouse = self.session.mouse

        if name == "goto":
            if not action.url:
                return {"success": False, "error": "URL required for goto"}
            # Allow file:// for local test/demo pages
            if not action.url.startswith("file://"):
                self.validate_url(action.url)
            await page.goto(action.url, wait_until="load")
            return {"success": True, "action": "goto", "url": action.url}

        elif name in ("click", "double_click", "right_click", "hover"):
            coords = await self._resolve_coords(action)
            if not coords:
                return {"success": False, "error": f"Could not resolve target for {name}"}
            
            x, y = coords
            if name == "click":
                await mouse.click(x, y)
            elif name == "double_click":
                await mouse.double_click(x, y)
            elif name == "right_click":
                await mouse.right_click(x, y)
            elif name == "hover":
                await mouse.hover(x, y)
            return {"success": True, "action": name, "target": (x, y)}

        elif name == "type":
            if action.element_id:
                coords = await self._resolve_coords(action)
                if coords:
                    await mouse.click(coords[0], coords[1])
            if action.text:
                await mouse.type_text(action.text)
            if action.submit:
                await page.keyboard.press("Enter")
            return {"success": True, "action": "type", "text": action.text}

        elif name == "press_key":
            if action.keys:
                await page.keyboard.press(action.keys)
            return {"success": True, "action": "press_key", "keys": action.keys}

        elif name == "scroll":
            direction = (action.direction or "down").lower()
            amount = action.amount or 400
            delta_y = amount if direction == "down" else -amount if direction == "up" else 0
            await mouse.smooth_scroll(delta_y)
            return {"success": True, "action": "scroll", "delta_y": delta_y}

        elif name == "wait":
            sec = action.seconds or 1.0
            await asyncio.sleep(sec)
            return {"success": True, "action": "wait", "seconds": sec}

        elif name == "go_back":
            await page.go_back()
            return {"success": True, "action": "go_back"}

        elif name == "done":
            return {"success": True, "action": "done", "summary": action.summary, "results": action.results}

        elif name == "ask_user":
            return {"success": True, "action": "ask_user", "question": action.question, "paused": True}

        return {"success": False, "error": f"Unknown action: {name}"}

    async def _resolve_coords(self, action: Action, retry: bool = True) -> Optional[tuple[float, float]]:
        if action.x is not None and action.y is not None:
            return (action.x, action.y)

        if action.element_id is not None:
            state = await self.perception.capture(self.session.page, vision_mode=False)
            for el in state.elements:
                if el.id == action.element_id:
                    box = el.bounding_box
                    return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

            if retry:
                # Element not found; DOM may have re-rendered or scrolled; wait briefly and try once more
                await asyncio.sleep(0.4)
                return await self._resolve_coords(action, retry=False)

        return None
