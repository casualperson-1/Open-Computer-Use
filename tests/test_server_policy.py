"""Server policy: decisions, approvals, rate limits and audit on RemoteComputer."""

import pytest

from nodes.remote import RemoteComputer
from security.policy import ServerNodePolicy
from shared.computer import ComputerError


class FakeHub:
    """Records dispatched commands; no socket needed."""

    def __init__(self):
        self.sent = []

    def connection(self, node_id=None):
        return None

    async def dispatch(self, name, params=None, *, timeout=30.0, node_id=None):
        self.sent.append((name, dict(params or {})))
        return {"ok": True}


def test_decisions_follow_lists_domains_apps_and_mode():
    policy = ServerNodePolicy(
        denied_commands=("key_combo",),
        allowed_domains=("example.com",),
        denied_apps=("steam",),
        approval_mode="risky",
        approval_commands=("open_item", "open_url"),
    )
    assert policy.decide("click", {}).outcome == "allow"
    assert policy.decide("key_combo", {}).outcome == "deny"
    assert policy.decide("open_url", {"url": "https://example.com/x"}).outcome == "confirm"
    assert policy.decide("open_url", {"url": "https://evil.test/x"}).outcome == "deny"
    assert policy.decide("open_item", {"target": "steam", "app_id": "steam"}).outcome == "deny"
    assert policy.decide("open_item", {"target": "/tmp/a", "app_id": "test.editor"}).outcome == "confirm"

    everything = ServerNodePolicy(approval_mode="all")
    assert everything.decide("screenshot", {}).outcome == "confirm"

    off = ServerNodePolicy()
    assert off.decide("open_item", {"app_id": "steam"}).outcome == "allow"

    allowlisted = ServerNodePolicy(allowed_commands=("click", "screenshot"))
    assert allowlisted.decide("type", {}).outcome == "deny"
    assert allowlisted.decide("click", {}).outcome == "allow"


def test_invalid_approval_mode_falls_back_to_off():
    assert ServerNodePolicy(approval_mode="sometimes").approval_mode == "off"


async def test_remote_computer_denies_before_dispatch_and_audits():
    hub = FakeHub()
    events = []

    async def audit(event):
        events.append(event)

    policy = ServerNodePolicy(denied_commands=("key_combo",))
    computer = RemoteComputer(hub, "n1", policy=policy, audit=audit)

    with pytest.raises(ComputerError) as excinfo:
        await computer.press_key("ctrl+alt+t")
    assert "denies" in str(excinfo.value)
    assert hub.sent == []
    assert events[-1]["outcome"] == "denied"
    assert events[-1]["command"] == "key_combo"


async def test_remote_computer_asks_for_approval_when_configured():
    hub = FakeHub()
    events = []
    asked = []

    async def audit(event):
        events.append(event)

    async def deny(name, params):
        asked.append((name, params))
        return False

    policy = ServerNodePolicy(approval_mode="risky", approval_commands=("open_item",))
    computer = RemoteComputer(hub, "n1", policy=policy, audit=audit, approve=deny)
    with pytest.raises(ComputerError):
        await computer.open_item("/tmp/a.pdf", app_id="test.editor")
    assert asked[0][0] == "open_item"
    assert hub.sent == []
    assert events[-1]["outcome"] == "rejected"

    async def allow(name, params):
        return True

    approving = RemoteComputer(
        hub, "n1", policy=policy, audit=audit, approve=allow
    )
    await approving.open_item("/tmp/a.pdf", app_id="test.editor")
    assert hub.sent[-1] == ("open_item", {"target": "/tmp/a.pdf", "app_id": "test.editor"})
    outcomes = [event["outcome"] for event in events]
    assert "approved" in outcomes
    assert outcomes[-1] == "ok"


async def test_remote_computer_skips_approval_when_a_run_already_asks():
    hub = FakeHub()

    async def must_not_be_called(name, params):  # pragma: no cover - assertion
        raise AssertionError("approval should have been skipped")

    policy = ServerNodePolicy(approval_mode="all")
    computer = RemoteComputer(
        hub, "n1", policy=policy, approve=must_not_be_called, skip_approval=True
    )
    await computer.click(10, 20)
    assert hub.sent[-1][0] == "click"


async def test_remote_computer_rate_limits_and_redacts_audit():
    hub = FakeHub()
    events = []

    async def audit(event):
        events.append(event)

    policy = ServerNodePolicy(rate_limit_per_minute=1)
    computer = RemoteComputer(hub, "n1", policy=policy, audit=audit)

    await computer.type_text("hunter2")
    with pytest.raises(ComputerError) as excinfo:
        await computer.type_text("again")
    assert "Rate limit" in str(excinfo.value)

    typed = events[0]
    assert typed["command"] == "type"
    assert typed["characters"] == 7
    assert "hunter2" not in str(typed)
    assert events[-1]["outcome"] == "throttled"


async def test_remote_computer_apps_use_app_id_and_kind():
    hub = FakeHub()
    computer = RemoteComputer(hub, "n1")
    await computer.list_apps(kind=".pdf")
    assert hub.sent[-1] == ("list_apps", {"kind": ".pdf"})

    await computer.open_item("/tmp/a.pdf", kind=".pdf", app_id="test.editor")
    assert hub.sent[-1] == (
        "open_item",
        {"target": "/tmp/a.pdf", "kind": ".pdf", "app_id": "test.editor"},
    )
