"""Typed wire protocol between the Open Computer Use server and a desktop node.

The server never accepts inbound connections. Nodes dial out over WebSocket
(``wss://…/ws/node``), authenticate with a per-node token, then exchange the
messages defined here: a ``hello``/``welcome`` handshake, heartbeats, typed
``command`` messages with request ids, and matching ``result`` messages.

Every command parameter set is a pydantic model, so the server and the node
reject malformed or out-of-range actions with the same errors before anything
touches a mouse or keyboard.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

PROTOCOL_VERSION = "1.0"

# Limits. These are protocol-level guards, not the full Phase 3 policy layer.
MAX_COORD = 100_000.0
MAX_TEXT_LENGTH = 4_000
MAX_PAGE_TEXT = 200_000
MAX_KEY_LENGTH = 120
MAX_TARGET_LENGTH = 2_000
MAX_SELECTOR_LENGTH = 500
DEFAULT_TIMEOUT_MS = 30_000
MAX_TIMEOUT_MS = 300_000
HEARTBEAT_INTERVAL_MS = 15_000

# WebSocket close codes used during the handshake.
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_UNSUPPORTED = 4402


class ProtocolError(ValueError):
    """Raised when a message or command cannot be accepted."""

    def __init__(self, message: str, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class CommandName(str, Enum):
    """Actions a node can perform on its own machine."""

    MOUSE_MOVE = "mouse_move"
    CLICK = "click"
    DRAG = "drag"
    SCROLL = "scroll"
    TYPE = "type"
    KEY_COMBO = "key_combo"
    SCREENSHOT = "screenshot"
    OPEN_URL = "open_url"
    BROWSER_ACTION = "browser_action"
    GET_PAGE_TEXT = "get_page_text"
    LIST_APPS = "list_apps"
    OPEN_ITEM = "open_item"
    DESCRIBE = "describe"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


def _validate_url(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("url is required")
    if len(text) > MAX_TARGET_LENGTH:
        raise ValueError("url is too long")
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("only http and https URLs are allowed")
    if not parsed.hostname:
        raise ValueError("url has no host")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")
    return text


def _validate_keys(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("keys is required")
    if len(text) > MAX_KEY_LENGTH:
        raise ValueError("keys is too long")
    parts = [part for part in text.split("+")]
    if not all(part.strip() for part in parts):
        raise ValueError("keys has an empty part")
    if not all(32 <= ord(char) < 127 for char in text):
        raise ValueError("keys must be printable ASCII")
    return text


# ---------------------------------------------------------------------------
# Command parameters
# ---------------------------------------------------------------------------


class EmptyParams(BaseModel):
    pass


class PointParams(BaseModel):
    x: float = Field(ge=0, le=MAX_COORD)
    y: float = Field(ge=0, le=MAX_COORD)


class ClickParams(PointParams):
    button: Literal["left", "right", "middle"] = "left"
    clicks: int = Field(default=1, ge=1, le=3)


class DragParams(BaseModel):
    from_x: float = Field(ge=0, le=MAX_COORD)
    from_y: float = Field(ge=0, le=MAX_COORD)
    to_x: float = Field(ge=0, le=MAX_COORD)
    to_y: float = Field(ge=0, le=MAX_COORD)


class ScrollParams(BaseModel):
    x: Optional[float] = Field(default=None, ge=0, le=MAX_COORD)
    y: Optional[float] = Field(default=None, ge=0, le=MAX_COORD)
    amount: int = Field(default=480, ge=-MAX_COORD, le=MAX_COORD)


class TypeParams(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class KeyComboParams(BaseModel):
    keys: str

    @field_validator("keys")
    @classmethod
    def _keys(cls, value: str) -> str:
        return _validate_keys(value)


class OpenUrlParams(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _validate_url(value)


class BrowserActionParams(BaseModel):
    action: Literal[
        "navigate",
        "back",
        "forward",
        "reload",
        "current_url",
        "title",
        "click",
        "fill",
        "press",
        "find_text",
    ]
    url: Optional[str] = None
    selector: Optional[str] = Field(default=None, max_length=MAX_SELECTOR_LENGTH)
    text: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    keys: Optional[str] = Field(default=None, max_length=MAX_KEY_LENGTH)
    query: Optional[str] = Field(default=None, max_length=MAX_SELECTOR_LENGTH)

    @model_validator(mode="after")
    def _requirements(self) -> "BrowserActionParams":
        if self.action == "navigate":
            self.url = _validate_url(self.url or "")
        if self.action in ("click", "fill"):
            if not (self.selector or "").strip():
                raise ValueError(f"{self.action} needs a selector")
        if self.action == "fill" and self.text is None:
            raise ValueError("fill needs text")
        if self.action == "press":
            self.keys = _validate_keys(self.keys or "")
        if self.action == "find_text" and not (self.query or "").strip():
            raise ValueError("find_text needs a query")
        return self


class GetPageTextParams(BaseModel):
    selector: Optional[str] = Field(default=None, max_length=MAX_SELECTOR_LENGTH)
    max_chars: int = Field(default=20_000, ge=1, le=MAX_PAGE_TEXT)


class ListAppsParams(BaseModel):
    query: Optional[str] = Field(default=None, max_length=200)


class OpenItemParams(BaseModel):
    target: str = Field(min_length=1, max_length=MAX_TARGET_LENGTH)
    kind: Optional[str] = Field(default=None, max_length=80)


class StopParams(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)


PARAM_MODELS: Dict[str, type[BaseModel]] = {
    CommandName.MOUSE_MOVE.value: PointParams,
    CommandName.CLICK.value: ClickParams,
    CommandName.DRAG.value: DragParams,
    CommandName.SCROLL.value: ScrollParams,
    CommandName.TYPE.value: TypeParams,
    CommandName.KEY_COMBO.value: KeyComboParams,
    CommandName.SCREENSHOT.value: EmptyParams,
    CommandName.OPEN_URL.value: OpenUrlParams,
    CommandName.BROWSER_ACTION.value: BrowserActionParams,
    CommandName.GET_PAGE_TEXT.value: GetPageTextParams,
    CommandName.LIST_APPS.value: ListAppsParams,
    CommandName.OPEN_ITEM.value: OpenItemParams,
    CommandName.DESCRIBE.value: EmptyParams,
    CommandName.PAUSE.value: StopParams,
    CommandName.RESUME.value: EmptyParams,
    CommandName.STOP.value: StopParams,
}


def command_names() -> list[str]:
    """Every command this protocol version understands, in enum order."""
    return [name.value for name in CommandName]


def validate_command(name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate and normalise one command's parameters.

    Raises :class:`ProtocolError` with ``code="unsupported_command"`` for an
    unknown command and ``code="invalid_params"`` for bad arguments.
    """
    model = PARAM_MODELS.get(name)
    if model is None:
        raise ProtocolError(f"Unsupported command '{name}'.", code="unsupported_command")
    try:
        parsed = model.model_validate(params or {})
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'params'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProtocolError(
            f"Invalid parameters for '{name}': {detail}", code="invalid_params"
        ) from exc
    return parsed.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    return uuid.uuid4().hex


class CommandMessage(BaseModel):
    """Server to node: run one command and answer with the same ``id``."""

    type: Literal["command"] = "command"
    id: str = Field(default_factory=new_request_id, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    params: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, ge=100, le=MAX_TIMEOUT_MS)
    run_id: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> "CommandMessage":
        self.params = validate_command(self.name, self.params)
        return self


class ResultMessage(BaseModel):
    """Node to server: the outcome of one :class:`CommandMessage`."""

    type: Literal["result"] = "result"
    id: str = Field(min_length=1, max_length=64)
    ok: bool = True
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = Field(default=None, max_length=2_000)
    error_code: Optional[str] = Field(default=None, max_length=64)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "ResultMessage":
        if self.ok and self.error:
            self.error = None
        if not self.ok and not self.error:
            self.error = "The node reported an unspecified failure."
        return self


class NodeHello(BaseModel):
    """Node to server: always the first message on a new connection."""

    type: Literal["hello"] = "hello"
    protocol: str = PROTOCOL_VERSION
    token: str = Field(min_length=16, max_length=200)
    node_id: Optional[str] = Field(default=None, max_length=40)
    name: str = Field(default="", max_length=80)
    platform: str = Field(default="", max_length=200)
    backend: str = Field(default="desktop", max_length=20)
    screen: Dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


class NodeWelcome(BaseModel):
    """Server to node: the handshake succeeded."""

    type: Literal["welcome"] = "welcome"
    node_id: str
    name: str = ""
    protocol: str = PROTOCOL_VERSION
    heartbeat_interval_ms: int = HEARTBEAT_INTERVAL_MS


class NodeError(BaseModel):
    """Server to node: the handshake or a message was rejected."""

    type: Literal["error"] = "error"
    code: str = "error"
    message: str = ""


class Heartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    ts: float = Field(default_factory=time.time)


class HeartbeatAck(BaseModel):
    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    ts: float = Field(default_factory=time.time)


WireMessage = Union[
    CommandMessage,
    ResultMessage,
    NodeHello,
    NodeWelcome,
    NodeError,
    Heartbeat,
    HeartbeatAck,
]


class _WireEnvelope(BaseModel):
    """Lenient envelope: the body is decoded by ``type`` afterwards."""

    type: str = Field(min_length=1, max_length=32)


def decode_message(raw: Union[str, bytes]) -> WireMessage:
    """Parse one JSON frame into a typed message, rejecting unknown types."""
    import json

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Frame is not valid UTF-8.", code="invalid_message") from exc
    if len(raw) > 32 * 1024 * 1024:
        raise ProtocolError("Frame is too large.", code="invalid_message")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Frame is not valid JSON.", code="invalid_message") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("Frame must be a JSON object.", code="invalid_message")

    envelope = _WireEnvelope.model_validate(payload)
    models = {
        "command": CommandMessage,
        "result": ResultMessage,
        "hello": NodeHello,
        "welcome": NodeWelcome,
        "error": NodeError,
        "heartbeat": Heartbeat,
        "heartbeat_ack": HeartbeatAck,
    }
    model = models.get(envelope.type)
    if model is None:
        raise ProtocolError(
            f"Unknown message type '{envelope.type}'.", code="unknown_message"
        )
    try:
        return model.model_validate(payload)  # type: ignore[return-value]
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'message'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProtocolError(
            f"Invalid '{envelope.type}' message: {detail}", code="invalid_message"
        ) from exc


def encode_message(message: BaseModel) -> str:
    return message.model_dump_json(exclude_none=True)


__all__ = [
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_UNAUTHORIZED",
    "CLOSE_UNSUPPORTED",
    "CommandMessage",
    "CommandName",
    "DEFAULT_TIMEOUT_MS",
    "HEARTBEAT_INTERVAL_MS",
    "Heartbeat",
    "HeartbeatAck",
    "MAX_TEXT_LENGTH",
    "NodeError",
    "NodeHello",
    "NodeWelcome",
    "PARAM_MODELS",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "ResultMessage",
    "WireMessage",
    "command_names",
    "decode_message",
    "encode_message",
    "new_request_id",
    "validate_command",
]
