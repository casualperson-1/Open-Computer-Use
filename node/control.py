"""Loopback-only control page for the node (pause, resume, stop).

Bound to 127.0.0.1 by default and gated by a random per-run token, so the page
is never exposed to the network even when the node has no other listener.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlsplit

from .audit import AuditLog
from .safety import SafetyState, write_secret_file

logger = logging.getLogger(__name__)

MAX_BODY = 8 * 1024
CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Open Computer Use node</title>
<style>
 body{{font:16px/1.5 system-ui,sans-serif;margin:0;padding:2rem;background:#101214;color:#e7e9ee}}
 h1{{font-size:1.15rem;margin:0 0 .25rem}}
 p{{color:#9aa3b2;margin:.25rem 0 1.25rem}}
 .state{{font-weight:600}}
 button{{font:inherit;padding:.6rem 1rem;margin:0 .5rem .5rem 0;border-radius:.5rem;
  border:1px solid #31363f;background:#1b1f26;color:#e7e9ee;cursor:pointer}}
 button.stop{{border-color:#7f1d1d;background:#2a0f12;color:#fca5a5}}
 button:disabled{{opacity:.5;cursor:default}}
 code{{background:#1b1f26;padding:.1rem .35rem;border-radius:.25rem}}
</style></head><body>
<h1>Open Computer Use node</h1>
<p><span class="state" id="state">…</span> <span id="detail"></span></p>
<div>
 <button id="pause">Pause</button>
 <button id="resume">Resume</button>
 <button id="stop" class="stop">Stop node</button>
</div>
<p>This page is only reachable from this machine. Stopping here cannot be undone
remotely: restart the node to allow control again.</p>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
async function call(path){{
  const response = await fetch(path, {{method:'POST', headers:{{'X-Node-Token':token}}}});
  if(!response.ok){{ alert('Request failed: ' + response.status); }}
  await refresh();
}}
async function refresh(){{
  const response = await fetch('/api/status', {{headers:{{'X-Node-Token':token}}}});
  if(!response.ok){{ document.getElementById('state').textContent = 'Not authorised'; return; }}
  const s = await response.json();
  const state = s.stopped ? 'Stopped' : s.paused ? 'Paused' : s.connected ? 'Connected' : 'Disconnected';
  document.getElementById('state').textContent = state;
  document.getElementById('detail').textContent =
    (s.node_id ? 'node ' + s.node_id : '') + (s.backend ? ' · ' + s.backend : '');
  document.getElementById('pause').disabled = s.paused || s.stopped;
  document.getElementById('resume').disabled = !s.paused || s.stopped;
  document.getElementById('stop').disabled = s.stopped;
}}
document.getElementById('pause').onclick = () => call('/api/pause');
document.getElementById('resume').onclick = () => call('/api/resume');
document.getElementById('stop').onclick = () => {{ if(confirm('Stop the node?')) call('/api/stop'); }};
refresh(); setInterval(refresh, 3000);
</script></body></html>
"""


class ControlServer:
    """Tiny HTTP server exposing the node's local kill switch."""

    def __init__(
        self,
        safety: SafetyState,
        status_provider: Callable[[], Dict[str, Any]],
        *,
        host: str = "127.0.0.1",
        port: int = 8791,
        token: Optional[str] = None,
        on_stop: Optional[Callable[[str], None]] = None,
        audit: Optional[AuditLog] = None,
    ) -> None:
        self.safety = safety
        self.status_provider = status_provider
        self.audit = audit or AuditLog.disabled()
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.on_stop = on_stop
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> int:
        handler = self._make_handler()
        server = ThreadingHTTPServer((self.host, self.port), handler)
        server.daemon_threads = True
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever, name="occ-node-control", daemon=True
        )
        self._thread.start()
        logger.info("Control page: http://%s:%d/?token=…", self.host, self.port)
        return self.port

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def write_token_file(self, path) -> None:
        write_secret_file(path, self.token)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    # -- request handling ------------------------------------------------
    def _authorised(self, headers, query: str) -> bool:
        supplied = headers.get("X-Node-Token") or ""
        if not supplied:
            supplied = (parse_qs(query).get("token") or [""])[0]
        return secrets.compare_digest(supplied, self.token)

    def _make_handler(self):
        control = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "occ-node"
            sys_version = ""

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                logger.debug("control: " + fmt, *args)

            # -- helpers
            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, status: int, payload: Dict[str, Any]) -> None:
                self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

            def _target(self):
                parsed = urlsplit(self.path)
                return parsed.path, parsed.query

            def _guard(self, path: str, query: str) -> bool:
                if not control._authorised(self.headers, query):
                    self._json(401, {"error": "unauthorised"})
                    return False
                if self.headers.get("Origin") and self.headers.get("Origin") not in (
                    f"http://{control.host}:{control.port}",
                ):
                    self._json(403, {"error": "cross-origin request blocked"})
                    return False
                return True

            # -- routes
            def do_GET(self) -> None:  # noqa: N802
                path, query = self._target()
                if path in ("/", "/index.html"):
                    if not control._authorised(self.headers, query):
                        self._json(401, {"error": "add ?token=… from the node log"})
                        return
                    self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/status":
                    if not self._guard(path, query):
                        return
                    payload = dict(control.status_provider() or {})
                    payload.update({"paused": control.safety.paused, "stopped": control.safety.stopped})
                    self._json(200, payload)
                    return
                if path == "/api/audit":
                    limit = 100
                    values = parse_qs(query).get("limit") or []
                    if values:
                        try:
                            limit = max(1, min(500, int(values[0])))
                        except ValueError:
                            limit = 100
                    self._json(200, {"events": control.audit.tail(limit)})
                    return
                if path == "/api/health":
                    self._json(200, {"status": "ok"})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                path, query = self._target()
                if not self._guard(path, query):
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._json(413, {"error": "body too large"})
                    return
                if length:
                    self.rfile.read(length)

                if path == "/api/pause":
                    control.safety.pause("Paused from the local control page.")
                    self._json(200, control.safety.snapshot())
                elif path == "/api/resume":
                    control.safety.resume()
                    self._json(200, control.safety.snapshot())
                elif path == "/api/stop":
                    control.safety.stop("Stopped from the local control page.")
                    if control.on_stop is not None:
                        control.on_stop("Stopped from the local control page.")
                    self._json(200, control.safety.snapshot())
                else:
                    self._json(404, {"error": "not found"})

            def do_HEAD(self) -> None:  # noqa: N802
                self.do_GET()

        return Handler


__all__ = ["ControlServer"]
