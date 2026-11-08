"""Node app contract: app_id/name only, policy-filtered, audit without text."""

import pytest

from node.audit import AuditLog
from node.config import NodeConfig
from node.drivers import apps as app_catalog
from node.executor import CommandExecutor
from node.policy import NodePolicy
from node.safety import SafetyState
from shared.policy import PolicyDenied
from shared.protocol import ProtocolError


def _config(tmp_path) -> NodeConfig:
    return NodeConfig(server_url="wss://example.test", token="t" * 32, data_dir=tmp_path)


def _executor(tmp_path, policy=None):
    config = _config(tmp_path)
    audit = AuditLog(config.audit_path, enabled=True)
    return CommandExecutor(
        config, SafetyState(), policy=policy or NodePolicy(), audit=audit
    )


APPS = [
    app_catalog.InstalledApp(
        id="test.editor",
        name="Test Editor",
        command="test-editor %f",
        mime_types=["application/pdf"],
    ),
    app_catalog.InstalledApp(
        id="test.browser",
        name="Test Browser",
        command="test-browser %u",
        mime_types=["x-scheme-handler/http"],
    ),
    app_catalog.InstalledApp(
        id="steam", name="Steam", command="steam", mime_types=[]
    ),
]


async def test_list_apps_returns_only_id_and_name_and_hides_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(app_catalog, "installed_apps", lambda: list(APPS))
    executor = _executor(tmp_path)

    data = await executor.execute("list_apps", {})
    assert data["apps"] == [
        {"app_id": "test.editor", "name": "Test Editor"},
        {"app_id": "test.browser", "name": "Test Browser"},
        {"app_id": "steam", "name": "Steam"},
    ]
    assert "command" not in data["apps"][0]
    assert "test-editor" not in str(data)


async def test_list_apps_honours_query_kind_and_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(app_catalog, "installed_apps", lambda: list(APPS))
    policy = NodePolicy(allowed_apps=("test.*",))
    executor = _executor(tmp_path, policy)

    data = await executor.execute("list_apps", {"kind": ".pdf"})
    assert data["apps"] == [{"app_id": "test.editor", "name": "Test Editor"}]

    data = await executor.execute("list_apps", {"query": "browser"})
    assert data["apps"] == [{"app_id": "test.browser", "name": "Test Browser"}]

    # "steam" is installed but not allowed to be seen or launched.
    data = await executor.execute("list_apps", {"query": "steam"})
    assert data["apps"] == []


async def test_open_item_rejects_unknown_and_denied_app_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(app_catalog, "installed_apps", lambda: list(APPS))
    monkeypatch.setattr(app_catalog, "find_app", lambda name: next(
        (app for app in APPS if app.id == name or app.name.lower() == name.lower()), None
    ))
    executor = _executor(tmp_path, NodePolicy(denied_apps=("steam",)))

    with pytest.raises(ProtocolError) as excinfo:
        await executor.execute("open_item", {"target": "/tmp/a.pdf", "app_id": "does.not.exist"})
    assert excinfo.value.code == "unknown_app"

    with pytest.raises(PolicyDenied) as excinfo:
        await executor.execute("open_item", {"target": "steam", "app_id": "steam"})
    assert excinfo.value.code == "app_not_allowed"

    events = executor.audit.tail(10)
    assert [event["outcome"] for event in events] == ["denied", "denied"]
    assert events[0]["code"] == "unknown_app"


async def test_open_item_launches_an_allowed_app_id(tmp_path, monkeypatch):
    monkeypatch.setattr(app_catalog, "installed_apps", lambda: list(APPS))
    monkeypatch.setattr(app_catalog, "find_app", lambda name: next(
        (app for app in APPS if app.id == name), None
    ))
    launched = []
    monkeypatch.setattr(
        app_catalog,
        "launch_app",
        lambda app, target=None: launched.append((app.id, target)) or "Launched.",
    )
    executor = _executor(tmp_path)
    pdf = tmp_path / "report.pdf"
    pdf.write_text("x")

    data = await executor.execute(
        "open_item", {"target": str(pdf), "app_id": "test.editor", "kind": ".pdf"}
    )
    assert data["app_id"] == "test.editor"
    assert launched == [("test.editor", str(pdf.resolve()))]
    assert executor.audit.tail(5)[-1]["outcome"] == "ok"


async def test_execute_enforces_policy_and_records_redacted_audit(tmp_path):
    executor = _executor(tmp_path, NodePolicy(denied_commands=("key_combo",)))

    with pytest.raises(PolicyDenied):
        await executor.execute("key_combo", {"keys": "ctrl+alt+t"})

    # `type` is allowed but must never put the text in the audit log.
    await executor.execute("type", {"text": "hunter2"})
    event = executor.audit.tail(1)[0]
    assert event["command"] == "type"
    assert event["characters"] == 7
    assert "hunter2" not in str(event)


async def test_rate_limit_stops_a_command_storm(tmp_path):
    executor = _executor(tmp_path, NodePolicy(rate_limit_per_minute=1))

    await executor.execute("mouse_move", {"x": 1, "y": 2})
    with pytest.raises(PolicyDenied) as excinfo:
        await executor.execute("mouse_move", {"x": 3, "y": 4})
    assert excinfo.value.code == "rate_limited"

    # The kill switch is never rate limited.
    await executor.execute("describe", {})


async def test_open_url_is_checked_against_the_domain_allowlist(tmp_path, monkeypatch):
    executor = _executor(tmp_path, NodePolicy(allowed_domains=("example.com",)))
    with pytest.raises(PolicyDenied) as excinfo:
        await executor.execute("open_url", {"url": "https://evil.test/"})
    assert excinfo.value.code == "domain_not_allowed"
