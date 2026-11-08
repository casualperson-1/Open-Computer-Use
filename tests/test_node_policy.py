"""Node-side policy: command/domain/app allowlists and how they are loaded."""

import json

import pytest

from node.config import ConfigError
from node.drivers.apps import InstalledApp
from node.policy import NodePolicy
from shared.policy import PolicyDenied


def test_default_policy_allows_everything_but_keeps_the_kill_switch():
    policy = NodePolicy()
    assert policy.command_allowed("click") is True
    assert policy.command_allowed("open_item") is True
    assert policy.summary() == "unrestricted"
    for always in ("describe", "pause", "resume", "stop"):
        assert policy.command_allowed(always) is True


def test_command_allow_and_deny_lists():
    policy = NodePolicy(
        allowed_commands=("mouse_move", "click", "stop"),
        denied_commands=("key_combo",),
        source="test",
    )
    assert policy.command_allowed("mouse_move") is True
    assert policy.command_allowed("key_combo") is False  # deny wins over allow
    assert policy.command_allowed("type") is False
    # Kill switch stays reachable even when not listed.
    assert policy.command_allowed("pause") is True

    with pytest.raises(PolicyDenied) as excinfo:
        policy.check_command("type")
    assert excinfo.value.code == "not_allowed"
    assert "policy" in str(excinfo.value)


def test_domain_rules_apply_to_urls_only():
    policy = NodePolicy(denied_domains=("*.bank.example",), allowed_domains=("example.com", "*.bank.example"))
    policy.check_url("https://example.com/ok")
    with pytest.raises(PolicyDenied) as excinfo:
        policy.check_url("https://www.bank.example/account")
    assert excinfo.value.code == "domain_not_allowed"

    open_policy = NodePolicy()
    open_policy.check_url("https://anywhere.test")


def test_app_allowlist_filters_listing_and_launching():
    apps = [
        InstalledApp(id="firefox", name="Firefox", command="firefox %u"),
        InstalledApp(id="org.gnome.Nautilus", name="Files", command="nautilus %f"),
        InstalledApp(id="steam", name="Steam", command="steam"),
    ]
    policy = NodePolicy(allowed_apps=("firefox", "org.gnome.*"))
    assert [app.id for app in policy.filter_apps(apps)] == ["firefox", "org.gnome.Nautilus"]
    assert policy.app_allowed("firefox", "Firefox") is True
    assert policy.app_allowed("steam", "Steam") is False

    denied = NodePolicy(denied_apps=("steam",))
    assert denied.app_allowed("steam", "Steam") is False
    assert denied.app_allowed("firefox", "Firefox") is True


def test_policy_loads_from_a_json_file(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "allowed_commands": ["mouse_move", "click"],
                "denied_domains": ["*.bank.example"],
                "rate_limit_per_minute": 30,
                "audit": False,
            }
        )
    )
    policy = NodePolicy.from_env(env={}, path=str(path))
    assert policy.allowed_commands == ("mouse_move", "click")
    assert policy.denied_domains == ("*.bank.example",)
    assert policy.rate_limit_per_minute == 30
    assert policy.audit is False
    assert policy.source == str(path)


def test_environment_overrides_the_file(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"allowed_commands": ["mouse_move"]}))
    policy = NodePolicy.from_env(
        env={
            "OCC_NODE_POLICY": str(path),
            "OCC_NODE_ALLOWED_COMMANDS": "click,scroll",
            "OCC_NODE_RATE_LIMIT": "12",
            "OCC_NODE_AUDIT": "false",
        }
    )
    assert policy.allowed_commands == ("click", "scroll")
    assert policy.rate_limit_per_minute == 12
    assert policy.audit is False
    assert "environment" in policy.source


def test_invalid_policy_configuration_is_rejected(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"allowed_app": ["firefox"]}))
    with pytest.raises(ConfigError) as excinfo:
        NodePolicy.from_env(env={}, path=str(path))
    assert "unknown keys" in str(excinfo.value)

    with pytest.raises(ConfigError):
        NodePolicy.from_env(env={"OCC_NODE_RATE_LIMIT": "lots"})
    with pytest.raises(ConfigError):
        NodePolicy.from_env(env={}, path=str(tmp_path / "missing.json"))

    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(ConfigError):
        NodePolicy.from_env(env={}, path=str(broken))
