"""Remote access: trusted forwarded IPs, Cloudflare Access, keepalive, reconnect.

The live tunnel simulation (nginx adding the edge headers) is described in
docs/remote-access.md; these tests cover the same logic without network
accounts or a running stack.
"""

import asyncio
import json
from pathlib import Path

import pytest
import websockets
from starlette.requests import Request
from starlette.responses import Response

import app as app_module
from node.client import NodeClient
from node.config import ConfigError, NodeConfig
from nodes.hub import STALE_AFTER_SECONDS
from security.network import (
    origin_allowed,
    parse_networks,
    resolve_client_ip,
    resolve_secure,
)
from shared.protocol import (
    HEARTBEAT_INTERVAL_MS,
    HeartbeatAck,
    NodeWelcome,
    decode_message,
    encode_message,
)

ROOT = Path(__file__).resolve().parents[1]


def _request(
    peer: str,
    *,
    scheme: str = "http",
    host: str = "127.0.0.1:8090",
    headers: dict | None = None,
) -> Request:
    raw = [(b"host", host.encode())]
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "root_path": "",
        "headers": raw,
        "client": (peer, 4321),
        "server": ("testserver", 80),
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Client IP and HTTPS detection through a tunnel
# ---------------------------------------------------------------------------


def test_cf_connecting_ip_requires_a_trusted_peer():
    trusted = parse_networks("172.28.0.0/24")
    assert (
        resolve_client_ip("172.28.0.5", "10.0.0.1", trusted, "203.0.113.50")
        == "203.0.113.50"
    )
    # The tunnel peer is trusted, so the CF header wins over X-Forwarded-For.
    assert (
        resolve_client_ip(
            "172.28.0.5", "198.51.100.7, 203.0.113.50", trusted, "203.0.113.50"
        )
        == "203.0.113.50"
    )
    # A direct request cannot choose its own throttling identity.
    assert (
        resolve_client_ip("198.51.100.7", "10.0.0.1", trusted, "203.0.113.50")
        == "198.51.100.7"
    )
    # Malformed or multi-hop values are ignored, not believed.
    assert (
        resolve_client_ip("172.28.0.5", "198.51.100.7", trusted, "not-an-ip")
        == "198.51.100.7"
    )
    assert (
        resolve_client_ip(
            "172.28.0.5", "198.51.100.7", trusted, "203.0.113.50, 10.0.0.1"
        )
        == "198.51.100.7"
    )


def test_app_uses_cf_ip_and_secure_proto_only_from_trusted_peers(monkeypatch):
    trusted = parse_networks("172.28.0.0/24")
    monkeypatch.setattr(app_module, "TRUSTED_PROXIES", trusted)
    edge = _request(
        "172.28.0.5",
        headers={
            "cf-connecting-ip": "203.0.113.50",
            "x-forwarded-for": "172.28.0.5",
            "x-forwarded-proto": "https",
        },
    )
    assert app_module._client_ip(edge) == "203.0.113.50"
    assert app_module._request_is_secure(edge) is True

    response = Response()
    app_module._set_session_cookie(response, edge, "session-token")
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie

    direct = _request(
        "198.51.100.7",
        headers={
            "cf-connecting-ip": "203.0.113.50",
            "x-forwarded-for": "10.0.0.1",
            "x-forwarded-proto": "https",
        },
    )
    assert app_module._client_ip(direct) == "198.51.100.7"
    assert app_module._request_is_secure(direct) is False
    direct_response = Response()
    app_module._set_session_cookie(direct_response, direct, "session-token")
    assert "secure" not in direct_response.headers["set-cookie"].lower()


def test_origin_checks_follow_the_public_base_url(monkeypatch):
    monkeypatch.setattr(app_module.settings, "PUBLIC_BASE_URL", "https://agent.example.test")
    good = _request(
        "172.28.0.5", headers={"origin": "https://agent.example.test"}
    )
    evil = _request("172.28.0.5", headers={"origin": "https://evil.example"})
    cross_site = _request(
        "172.28.0.5",
        headers={"origin": "https://agent.example.test", "sec-fetch-site": "cross-site"},
    )
    assert app_module._request_origin_ok(good) is True
    assert app_module._request_origin_ok(evil) is False
    assert app_module._request_origin_ok(cross_site) is False

    # Caddy may rewrite the upstream Host; PUBLIC_BASE_URL still matches.
    assert origin_allowed("https://agent.example.test", "server:8765", "https://agent.example.test")
    assert resolve_secure("http", "https", "172.28.0.5", parse_networks("172.28.0.0/24"))


# ---------------------------------------------------------------------------
# Keepalive
# ---------------------------------------------------------------------------


def test_heartbeat_is_far_below_the_cloudflare_idle_limit():
    assert 0 < HEARTBEAT_INTERVAL_MS <= 30_000
    assert HEARTBEAT_INTERVAL_MS < 100_000
    assert STALE_AFTER_SECONDS == 60.0
    assert STALE_AFTER_SECONDS < 100.0
    assert HEARTBEAT_INTERVAL_MS / 1000.0 < STALE_AFTER_SECONDS


# ---------------------------------------------------------------------------
# Cloudflare Access headers on the node
# ---------------------------------------------------------------------------


def test_node_sends_access_headers_and_never_exposes_them(tmp_path, monkeypatch):
    monkeypatch.delenv("OCC_NODE_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("OCC_NODE_CF_ACCESS_CLIENT_SECRET", raising=False)
    id_file = tmp_path / "access-id"
    secret_file = tmp_path / "access-secret"
    id_file.write_text("id-from-file.access", encoding="utf-8")
    secret_file.write_text("secret-from-file-value", encoding="utf-8")
    monkeypatch.setenv("OCC_NODE_CF_ACCESS_CLIENT_ID_FILE", str(id_file))
    monkeypatch.setenv("OCC_NODE_CF_ACCESS_CLIENT_SECRET_FILE", str(secret_file))

    config = NodeConfig.from_env(
        server="https://agent.example.test",
        token="t" * 40,
        data_dir=str(tmp_path / "state"),
    )
    assert config.access_headers == {
        "CF-Access-Client-Id": "id-from-file.access",
        "CF-Access-Client-Secret": "secret-from-file-value",
    }
    assert "access-id-from-file" not in repr(config)
    assert "secret-from-file-value" not in repr(config)

    client = NodeClient(config)
    try:
        assert "secret-from-file-value" not in json.dumps(client.status())
    finally:
        asyncio.run(client.close())


def test_node_access_headers_require_both_halves(tmp_path, monkeypatch):
    monkeypatch.delenv("OCC_NODE_CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("OCC_NODE_CF_ACCESS_CLIENT_ID", "id-only.access")
    with pytest.raises(ConfigError, match="both"):
        NodeConfig.from_env(
            server="https://agent.example.test",
            token="t" * 40,
            data_dir=str(tmp_path / "state"),
        )


# ---------------------------------------------------------------------------
# Idle channel and reconnect, through a local relay that can be restarted
# ---------------------------------------------------------------------------


class FakeExecutor:
    async def status(self) -> dict:
        return {"platform": "Linux", "backend": "desktop", "screen": {}, "capabilities": []}

    async def execute(self, name, params):  # pragma: no cover - not used
        return {}

    async def close(self) -> None:
        return None


class FakeServer:
    """A minimal protocol server: one welcome, then heartbeat acknowledgements."""

    def __init__(self) -> None:
        self.hellos = 0
        self.heartbeats = 0
        self.access_headers: list[dict] = []
        self.hello_tokens: list[str] = []
        self._connected = asyncio.Event()
        self._server = None
        self.port = 0

    async def start(self) -> None:
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0, ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, socket) -> None:
        headers = {
            "CF-Access-Client-Id": socket.request.headers.get("CF-Access-Client-Id"),
            "CF-Access-Client-Secret": socket.request.headers.get("CF-Access-Client-Secret"),
        }
        self.access_headers.append(headers)
        try:
            hello = decode_message(await socket.recv())
        except Exception:
            return
        self.hellos += 1
        self.hello_tokens.append(getattr(hello, "token", ""))
        self._connected.set()
        await socket.send(
            encode_message(
                NodeWelcome(node_id="node-test", name="test", heartbeat_interval_ms=2_000)
            )
        )
        try:
            async for raw in socket:
                message = decode_message(raw)
                if message.type == "heartbeat":
                    self.heartbeats += 1
                    await socket.send(encode_message(HeartbeatAck(ts=message.ts)))
        except Exception:
            pass

    async def wait_connected(self, timeout: float = 10.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)
        self._connected.clear()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


class Relay:
    """TCP proxy in front of the fake server; stopping it drops the tunnel."""

    def __init__(self, target_port: int) -> None:
        self.target_port = target_port
        self._server = None
        self._writers: set = set()
        self.port = 0

    async def start(self, port: int = 0) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            "127.0.0.1", self.target_port
        )
        self._writers.update({writer, upstream_writer})

        async def pipe(source, target) -> None:
            try:
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
            except Exception:
                pass
            finally:
                try:
                    target.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe(reader, upstream_writer), pipe(upstream_reader, writer)
        )

    async def stop(self) -> None:
        # Close the streams first: on Python 3.12+ Server.wait_closed() waits
        # for active connection handlers, and those handlers are the relay
        # pipes that only finish once these writers are closed.
        for writer in list(self._writers):
            try:
                writer.close()
            except Exception:
                pass
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.05)


async def test_idle_connection_survives_and_reconnects_after_a_tunnel_restart(tmp_path):
    server = FakeServer()
    await server.start()
    relay = Relay(server.port)
    await relay.start()
    config = NodeConfig(
        server_url=f"http://127.0.0.1:{relay.port}",
        token="t" * 40,
        data_dir=tmp_path,
        reconnect_max_seconds=2.0,
        cf_access_client_id="id.access",
        cf_access_client_secret="secret-value",
    )
    client = NodeClient(config, executor=FakeExecutor())
    task = asyncio.create_task(client.run())
    try:
        await server.wait_connected()
        await _wait_until(lambda: server.heartbeats >= 1, timeout=6.0)
        # Still connected after an idle period longer than one heartbeat.
        await asyncio.sleep(2.5)
        assert client.connected is True
        assert server.hellos == 1

        # The relay is the tunnel: kill it, then bring it back on the same port.
        await relay.stop()
        await _wait_until(lambda: client.connected is False, timeout=5.0)
        await relay.start(relay.port)
        await server.wait_connected(timeout=10.0)
        await _wait_until(lambda: client.connected is True, timeout=5.0)
        assert server.hellos >= 2

        # Access headers travel with the handshake, and the node token is still
        # the credential the server checks.
        assert server.access_headers[0]["CF-Access-Client-Id"] == "id.access"
        assert server.access_headers[0]["CF-Access-Client-Secret"] == "secret-value"
        assert server.hello_tokens[0] == "t" * 40
    finally:
        client.request_stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
        await client.close()
        await relay.stop()
        await server.close()


# ---------------------------------------------------------------------------
# Compose / docs assets
# ---------------------------------------------------------------------------


def test_compose_declares_tunnels_with_file_secrets_and_no_literal_tokens():
    import yaml

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    secrets = compose["secrets"]
    for name in ("cf_tunnel_token", "twingate_access_token", "twingate_refresh_token"):
        assert name in secrets
        assert secrets[name]["file"].startswith("./deploy/secrets/")

    cloudflared = compose["services"]["cloudflared"]
    assert cloudflared["profiles"] == ["cloudflared"]
    assert cloudflared["secrets"] == ["cf_tunnel_token"]
    assert "ports" not in cloudflared
    assert cloudflared["environment"] == {"TUNNEL_TOKEN_FILE": "/run/secrets/cf_tunnel_token"}
    assert all(
        "/run/secrets/" in value for value in cloudflared["environment"].values()
    )

    twingate = compose["services"]["twingate"]
    assert twingate["profiles"] == ["twingate"]
    assert "ports" not in twingate
    assert set(twingate["secrets"]) == {
        "twingate_access_token",
        "twingate_refresh_token",
    }
    assert "NET_ADMIN" in twingate["cap_add"]

    tunnel_sim = compose["services"]["tunnel-sim"]
    assert "tunnelsim" in tunnel_sim["profiles"]
    assert "127.0.0.1:" in tunnel_sim["ports"][0]

    caddyfile = (ROOT / "deploy" / "Caddyfile.compose").read_text(encoding="utf-8")
    assert "trusted_proxies" in caddyfile
    script = (ROOT / "deploy" / "generate-secrets.sh").read_text(encoding="utf-8")
    for name in ("cf_tunnel_token", "twingate_access_token", "twingate_refresh_token"):
        assert name in script
