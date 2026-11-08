"""Shared policy helpers: patterns, domains, rate limiting and audit redaction."""

import pytest

from shared.policy import (
    PolicyDenied,
    RateLimiter,
    describe_command,
    domain_matches,
    host_of,
    matches_any,
    redact_url,
    split_patterns,
    url_allowed,
)


def test_patterns_are_normalised_and_deduplicated():
    assert split_patterns(" Click , type ,, click ") == ("click", "type")
    assert split_patterns(None) == ()
    assert split_patterns(["open_*", "OPEN_*"]) == ("open_*",)
    assert matches_any("open_url", ("open_*",)) is True
    assert matches_any("open_url", ()) is False
    assert matches_any("", ("*",)) is False


def test_domains_match_exactly_wildcards_and_apex():
    assert host_of("https://Example.COM/path?x=1") == "example.com"
    assert host_of("example.com") == "example.com"
    assert host_of("not a url") == ""

    assert domain_matches("example.com", ("example.com",)) is True
    assert domain_matches("a.example.com", ("example.com",)) is False
    assert domain_matches("a.example.com", ("*.example.com",)) is True
    assert domain_matches("example.com", ("*.example.com",)) is True
    assert domain_matches("evil-example.com", ("*.example.com",)) is False
    assert domain_matches("anything.test", ("*",)) is True
    assert domain_matches("example.com", ()) is False


def test_url_policy_allows_everything_until_a_list_is_set():
    assert url_allowed("https://example.com", (), ()) is True
    assert url_allowed("https://example.com", ("example.com",), ()) is True
    assert url_allowed("https://other.test", ("example.com",), ()) is False
    # A deny entry wins over an open allow list.
    assert url_allowed("https://bank.example", (), ("*.bank.example",)) is False
    assert url_allowed("not a url", (), ()) is False


def test_rate_limiter_buckets_and_reports_wait():
    unlimited = RateLimiter(0)
    assert [unlimited.allow() for _ in range(100)] == [True] * 100
    assert unlimited.retry_after() == 0.0

    limiter = RateLimiter(per_minute=2)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False
    assert limiter.retry_after() > 0

    with pytest.raises(PolicyDenied) as excinfo:
        limiter.check("commands")
    assert excinfo.value.code == "rate_limited"
    assert "Try again" in str(excinfo.value)


def test_audit_summaries_never_contain_typed_text_or_url_queries():
    typed = describe_command("type", {"text": "hunter2 secret"})
    assert typed == {"command": "type", "characters": 14}
    assert "hunter2" not in str(typed)

    navigated = describe_command("open_url", {"url": "https://example.com/a?token=abc#frag"})
    assert navigated["url"] == "https://example.com/a"

    filled = describe_command(
        "browser_action", {"action": "fill", "selector": "#pw", "text": "hunter2"}
    )
    assert filled["characters"] == 7
    assert "hunter2" not in str(filled)

    launched = describe_command(
        "open_item", {"target": "/tmp/a.pdf", "kind": ".pdf", "app_id": "test.editor"}
    )
    assert launched == {
        "command": "open_item",
        "target": "/tmp/a.pdf",
        "kind": ".pdf",
        "app_id": "test.editor",
    }
    assert redact_url("https://example.com/x?y=1") == "https://example.com/x"
