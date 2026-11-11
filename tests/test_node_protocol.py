import json

import pytest

from node.client import backoff_delay
from shared.protocol import (
    PROTOCOL_VERSION,
    CommandMessage,
    Heartbeat,
    NodeHello,
    NodeWelcome,
    ProtocolError,
    ResultMessage,
    ScreenFrame,
    ScreenStreamRequest,
    decode_message,
    encode_message,
    validate_command,
)


def test_command_round_trip_keeps_request_id():
    message = CommandMessage(id="req-1", name="click", params={"x": 10, "y": 20})
    decoded = decode_message(encode_message(message))
    assert isinstance(decoded, CommandMessage)
    assert decoded.id == "req-1"
    assert decoded.params == {"x": 10.0, "y": 20.0, "button": "left", "clicks": 1}


def test_handshake_messages_round_trip():
    hello = decode_message(
        encode_message(
            NodeHello(
                token="t" * 32,
                name="desk",
                backend="desktop",
                platform="Linux",
                capabilities=["click"],
            )
        )
    )
    assert isinstance(hello, NodeHello)
    assert hello.protocol == PROTOCOL_VERSION
    assert hello.backend == "desktop"

    welcome = decode_message(encode_message(NodeWelcome(node_id="abc")))
    assert isinstance(welcome, NodeWelcome)
    assert welcome.heartbeat_interval_ms > 0


def test_heartbeat_is_timestamped():
    beat = Heartbeat()
    assert beat.ts > 0
    assert decode_message(encode_message(beat)).type == "heartbeat"


def test_live_view_messages_round_trip():
    request = decode_message(
        encode_message(ScreenStreamRequest(enabled=True, fps=2.5, quality=55))
    )
    assert isinstance(request, ScreenStreamRequest)
    assert request.enabled is True
    assert request.fps == 2.5

    frame = decode_message(
        encode_message(
            ScreenFrame(
                data="data:image/jpeg;base64," + "A" * 64,
                width=1280,
                height=720,
                screen_width=1920,
                screen_height=1080,
                cursor=[10, 20],
            )
        )
    )
    assert isinstance(frame, ScreenFrame)
    assert frame.width == 1280
    assert frame.screen_width == 1920
    assert frame.data.startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "name, params",
    [
        ("mouse_move", {"x": -1, "y": 10}),
        ("mouse_move", {"x": "left", "y": 10}),
        ("click", {"x": 1, "y": 2, "button": "elbow"}),
        ("click", {"x": 1, "y": 2, "clicks": 9}),
        ("scroll", {"amount": "lots"}),
        ("type", {"text": ""}),
        ("type", {"text": "x" * 5000}),
        ("key_combo", {"keys": ""}),
        ("key_combo", {"keys": "ctrl+alt+"}),
        ("open_url", {"url": "javascript:alert(1)"}),
        ("open_url", {"url": "file:///etc/passwd"}),
        ("open_url", {"url": "https://user:pass@example.com"}),
        ("browser_action", {"action": "click"}),
        ("browser_action", {"action": "fill", "selector": "#q"}),
        ("browser_action", {"action": "teleport"}),
        ("open_item", {}),
    ],
)
def test_invalid_commands_are_rejected(name, params):
    with pytest.raises(ProtocolError) as excinfo:
        validate_command(name, params)
    assert excinfo.value.code == "invalid_params"


def test_unknown_command_is_rejected():
    with pytest.raises(ProtocolError) as excinfo:
        validate_command("format_disk", {})
    assert excinfo.value.code == "unsupported_command"


def test_open_url_requires_http_scheme():
    assert validate_command("open_url", {"url": "https://example.com/a?b=1"}) == {
        "url": "https://example.com/a?b=1"
    }


def test_command_message_validates_parameters():
    with pytest.raises(Exception):
        CommandMessage(name="type", params={"text": ""})


def test_result_message_fills_or_drops_error():
    ok = ResultMessage(id="1", ok=True, error="stale")
    assert ok.error is None
    failed = ResultMessage(id="2", ok=False)
    assert failed.error


def test_decode_rejects_junk():
    with pytest.raises(ProtocolError):
        decode_message("not json")
    with pytest.raises(ProtocolError):
        decode_message(json.dumps({"type": "nope"}))
    with pytest.raises(ProtocolError):
        decode_message(json.dumps([1, 2, 3]))
    with pytest.raises(ProtocolError):
        decode_message(json.dumps({"type": "result"}))


def test_backoff_grows_and_is_capped():
    delays = [backoff_delay(attempt, base=1.0, cap=8.0, rng=lambda: 0.5) for attempt in range(1, 8)]
    assert delays[0] == pytest.approx(1.0)
    assert delays[1] == pytest.approx(2.0)
    assert delays[2] == pytest.approx(4.0)
    assert all(delay <= 8.0 for delay in delays)
    assert delays[-1] == pytest.approx(8.0)


def test_backoff_is_jittered():
    low = backoff_delay(3, base=1.0, cap=60.0, rng=lambda: 0.0)
    high = backoff_delay(3, base=1.0, cap=60.0, rng=lambda: 0.999999)
    assert low < high
