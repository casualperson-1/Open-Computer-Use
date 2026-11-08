import json
import urllib.error
import urllib.request

import pytest

from node.config import NodeConfig, normalise_server_url
from node.control import ControlServer
from node.safety import SafetyState
from shared.protocol import PROTOCOL_VERSION, ResultMessage, encode_message


class FakeSocket:
    """Enough of a websockets connection for the client's handshake."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if not self.replies:
            raise ConnectionError("closed")
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else encode_message(reply)


def _config(**overrides):
    base = dict(
        server_url="https://agent.example.com",
        token="t" * 40,
        name="desk",
        data_dir=overrides.pop("data_dir"),
    )
    base.update(overrides)
    return NodeConfig(**base)


def test_server_url_normalisation():
    assert normalise_server_url("https://agent.example.com") == "wss://agent.example.com/ws/node"
    assert normalise_server_url("agent.example.com") == "wss://agent.example.com/ws/node"
    assert normalise_server_url("http://10.0.0.5:8765/") == "ws://10.0.0.5:8765/ws/node"
    assert normalise_server_url("wss://host/ws/node") == "wss://host/ws/node"
    with pytest.raises(Exception):
        normalise_server_url("ftp://host")
    with pytest.raises(Exception):
        normalise_server_url("https://user:pass@host")


def test_config_rejects_short_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("OCC_NODE_TOKEN", "short")
    with pytest.raises(Exception):
        NodeConfig.from_env(server="https://host", data_dir=str(tmp_path))


async def test_handshake_sends_token_and_accepts_welcome(tmp_path):
    from node.client import NodeClient
    from shared.protocol import NodeWelcome

    config = _config(data_dir=tmp_path)
    client = NodeClient(config, SafetyState())
    socket = FakeSocket([NodeWelcome(node_id="n-1", name="desk")])

    welcome = await client._handshake(socket)
    assert welcome.node_id == "n-1"
    hello = socket.sent[0]
    assert hello["type"] == "hello"
    assert hello["token"] == "t" * 40
    assert hello["protocol"] == PROTOCOL_VERSION


async def test_unauthorized_handshake_stops_retrying(tmp_path):
    from node.client import NodeAuthError, NodeClient
    from shared.protocol import NodeError

    client = NodeClient(_config(data_dir=tmp_path), SafetyState())
    socket = FakeSocket([NodeError(code="unauthorized", message="bad token")])
    with pytest.raises(NodeAuthError):
        await client._handshake(socket)


async def test_run_command_answers_with_the_same_id(tmp_path):
    from node.client import NodeClient
    from shared.protocol import CommandMessage

    class Executor:
        async def status(self):
            return {}

        async def execute(self, name, params):
            return {"clicked": params}

        async def close(self):
            return None

    client = NodeClient(_config(data_dir=tmp_path), SafetyState(), executor=Executor())
    socket = FakeSocket([])
    message = CommandMessage(name="click", params={"x": 3, "y": 4})
    await client._run_command(socket, message)

    result = socket.sent[-1]
    assert result["type"] == "result"
    assert result["id"] == message.id
    assert result["ok"] is True
    assert result["data"]["clicked"]["x"] == 3.0


async def test_run_command_reports_a_paused_node(tmp_path):
    from node.client import NodeClient
    from node.safety import NodeHalted
    from shared.protocol import CommandMessage

    class Executor:
        async def status(self):
            return {}

        async def execute(self, name, params):
            raise NodeHalted("The node is paused locally.", code="paused")

        async def close(self):
            return None

    client = NodeClient(_config(data_dir=tmp_path), SafetyState(), executor=Executor())
    socket = FakeSocket([])
    await client._run_command(socket, CommandMessage(name="screenshot"))
    result = socket.sent[-1]
    assert result["ok"] is False
    assert result["error_code"] == "paused"


def test_safety_state_is_sticky():
    state = SafetyState()
    state.pause()
    assert state.paused is True
    state.resume()
    assert state.paused is False
    state.stop()
    assert state.stopped is True
    assert state.resume() is False
    assert state.paused is True


# ---------------------------------------------------------------------------
# Control page
# ---------------------------------------------------------------------------


def _request(url, token=None, method="GET"):
    headers = {"X-Node-Token": token} if token else {}
    request = urllib.request.Request(url, method=method, headers=headers, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode() or "{}")


def test_control_page_requires_the_token(tmp_path):
    safety = SafetyState()
    control = ControlServer(safety, lambda: {"connected": True}, port=0)
    port = control.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _request(f"http://127.0.0.1:{port}/api/status")
        assert excinfo.value.code == 401

        status, payload = _request(f"http://127.0.0.1:{port}/api/status", control.token)
        assert status == 200
        assert payload["connected"] is True
    finally:
        control.stop()


def test_control_page_stop_is_a_kill_switch(tmp_path):
    safety = SafetyState()
    stopped = []
    control = ControlServer(
        safety, lambda: {"connected": True}, port=0, on_stop=stopped.append
    )
    port = control.start()
    try:
        status, payload = _request(
            f"http://127.0.0.1:{port}/api/stop", control.token, method="POST"
        )
        assert status == 200
        assert payload["stopped"] is True
        assert safety.stopped is True
        assert stopped
    finally:
        control.stop()


def test_control_token_file_is_private(tmp_path):
    safety = SafetyState()
    control = ControlServer(safety, lambda: {}, port=0)
    path = tmp_path / "control.token"
    control.write_token_file(path)
    assert path.read_text(encoding="utf-8") == control.token
    assert oct(path.stat().st_mode)[-3:] == "600"
