import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from nodes.hub import (
    NodeCommandError,
    NodeConnection,
    NodeHub,
    NodeTimeout,
    NodeUnavailable,
)
from nodes.registry import NodeRegistry, hash_node_token
from shared.protocol import CommandMessage, ResultMessage


class FakeWebSocket:
    """Minimal stand-in for a Starlette WebSocket on the server side."""

    def __init__(self) -> None:
        self.sent = []
        self.closed = None
        self.fail = False

    async def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed = code


@pytest.fixture
async def registry(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nodes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield NodeRegistry(factory)
    await engine.dispose()


async def _wait_for_send(connection, count=1):
    for _ in range(50):
        if len(connection.websocket.sent) >= count:
            return connection.websocket.sent[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("the hub did not send a command")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


async def test_registry_stores_only_token_hashes(registry):
    record, token = await registry.create("office-pc")
    assert record.name == "office-pc"
    assert len(token) >= 32
    assert record.token_hash == hash_node_token(token)
    assert token not in record.token_hash

    found = await registry.find_by_token(token)
    assert found is not None and found.id == record.id
    assert await registry.find_by_token("nope-nope-nope-nope") is None


async def test_rotation_invalidates_the_old_token(registry):
    record, old_token = await registry.create("desk")
    new_token = await registry.rotate(record.id)
    assert new_token and new_token != old_token
    assert await registry.find_by_token(old_token) is None
    assert (await registry.find_by_token(new_token)).id == record.id


async def test_revocation_blocks_authentication(registry):
    record, token = await registry.create("desk")
    assert await registry.revoke(record.id) is True
    assert await registry.find_by_token(token) is None
    assert await registry.revoke(record.id) is False
    assert await registry.rotate(record.id) is None


async def test_payload_never_leaks_the_token(registry):
    record, token = await registry.create("desk")
    payload = registry.payload(record)
    assert token not in json.dumps(payload)
    assert record.token_hash not in json.dumps(payload)
    assert payload["revoked"] is False


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


async def test_dispatch_routes_the_result_by_request_id():
    hub = NodeHub()
    connection = NodeConnection("n1", FakeWebSocket(), name="desk")
    await hub.register(connection)

    task = asyncio.create_task(hub.dispatch("screenshot", {}))
    sent = await _wait_for_send(connection)
    assert sent["type"] == "command"
    assert sent["name"] == "screenshot"

    connection.resolve(ResultMessage(id=sent["id"], ok=True, data={"width": 10}))
    assert await task == {"width": 10}


async def test_dispatch_raises_on_node_failure():
    hub = NodeHub()
    connection = NodeConnection("n1", FakeWebSocket())
    await hub.register(connection)

    task = asyncio.create_task(hub.dispatch("click", {"x": 1, "y": 1}))
    sent = await _wait_for_send(connection)
    connection.resolve(
        ResultMessage(id=sent["id"], ok=False, error="paused", error_code="paused")
    )
    with pytest.raises(NodeCommandError) as excinfo:
        await task
    assert excinfo.value.code == "paused"


async def test_dispatch_times_out():
    hub = NodeHub()
    connection = NodeConnection("n1", FakeWebSocket())
    await hub.register(connection)

    with pytest.raises(NodeTimeout):
        await hub.dispatch("screenshot", {}, timeout=0.05)


async def test_dispatch_without_a_node_is_rejected():
    hub = NodeHub()
    with pytest.raises(NodeUnavailable):
        await hub.dispatch("screenshot", {})


async def test_invalid_parameters_never_reach_the_node():
    hub = NodeHub()
    connection = NodeConnection("n1", FakeWebSocket())
    await hub.register(connection)
    with pytest.raises(NodeCommandError):
        await hub.dispatch("mouse_move", {"x": -5, "y": 1})
    assert connection.websocket.sent == []


async def test_unregister_fails_pending_commands():
    hub = NodeHub()
    connection = NodeConnection("n1", FakeWebSocket())
    await hub.register(connection)

    task = asyncio.create_task(hub.dispatch("screenshot", {}))
    await _wait_for_send(connection)
    hub.unregister(connection)
    with pytest.raises(NodeUnavailable):
        await task
    assert hub.summary()["connected"] is False


async def test_summary_reports_the_primary_node():
    hub = NodeHub()
    await hub.register(
        NodeConnection("n1", FakeWebSocket(), name="desk", backend="browser", platform="Linux")
    )
    summary = hub.summary()
    assert summary["connected"] is True
    assert summary["node_id"] == "n1"
    assert summary["backend"] == "browser"
    assert summary["nodes"] == 1


# ---------------------------------------------------------------------------
# /ws/node endpoint
# ---------------------------------------------------------------------------


def _app_module():
    import app as app_module

    return app_module


class _Record:
    id = "node-1"
    name = "lab"
    backend = "desktop"
    platform = "Linux"
    revoked_at = None


class _Registry:
    def __init__(self, record=None):
        self.record = record

    async def find_by_token(self, token):
        return self.record if self.record is not None else None

    async def touch(self, *args, **kwargs):
        return None


def test_node_socket_rejects_an_unknown_token(monkeypatch):
    app_module = _app_module()
    monkeypatch.setattr(app_module, "node_registry", lambda: _Registry(None))
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws/node") as socket:
            socket.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": "1.0",
                        "token": "z" * 40,
                        "name": "intruder",
                    }
                )
            )
            message = json.loads(socket.receive_text())
            assert message["type"] == "error"
            assert message["code"] == "unauthorized"


def test_node_socket_completes_the_handshake(monkeypatch):
    app_module = _app_module()
    monkeypatch.setattr(app_module, "node_registry", lambda: _Registry(_Record()))
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws/node") as socket:
            socket.send_text(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": "1.0",
                        "token": "y" * 40,
                        "name": "lab",
                        "backend": "desktop",
                        "platform": "Linux",
                        "capabilities": ["click"],
                    }
                )
            )
            welcome = json.loads(socket.receive_text())
            assert welcome["type"] == "welcome"
            assert welcome["node_id"] == "node-1"

            socket.send_text(json.dumps({"type": "heartbeat"}))
            ack = json.loads(socket.receive_text())
            assert ack["type"] == "heartbeat_ack"

    assert app_module.node_hub.summary()["connected"] is False


def test_node_socket_rejects_a_wrong_protocol_version(monkeypatch):
    app_module = _app_module()
    monkeypatch.setattr(app_module, "node_registry", lambda: _Registry(_Record()))
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws/node") as socket:
            socket.send_text(
                json.dumps(
                    {"type": "hello", "protocol": "9.0", "token": "y" * 40, "name": "lab"}
                )
            )
            message = json.loads(socket.receive_text())
            assert message["type"] == "error"
            assert message["code"] == "unsupported_protocol"


def test_command_message_ids_are_unique():
    ids = {CommandMessage(name="screenshot").id for _ in range(50)}
    assert len(ids) == 50
