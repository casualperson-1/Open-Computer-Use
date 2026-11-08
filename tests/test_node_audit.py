"""The node audit log: append-only JSONL, permissions, rotation, best effort."""

import json
import stat

from node.audit import AuditLog


def test_events_are_appended_in_order_and_private(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record({"command": "click", "outcome": "ok", "x": 10, "y": 20})
    log.record({"event": "connected", "node_id": "n1"})

    events = log.tail(10)
    assert [event["command"] if "command" in event else event["event"] for event in events] == [
        "click",
        "connected",
    ]
    assert events[0]["source"] == "node"
    assert events[0]["ts"]

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_tail_limits_and_tolerates_bad_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for index in range(5):
        log.record({"event": "tick", "index": index})
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json\n")

    events = log.tail(2)
    assert [event["index"] for event in events] == [3, 4]


def test_disabled_log_is_a_no_op(tmp_path):
    log = AuditLog.disabled()
    log.record({"command": "click"})
    assert log.tail(10) == []

    path = tmp_path / "audit.jsonl"
    quiet = AuditLog(path, enabled=False)
    quiet.record({"command": "click"})
    assert not path.exists()


def test_log_rotates_once_past_the_limit(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, max_bytes=64 * 1024)
    log.record({"event": "big", "detail": "x" * 70_000})
    assert path.exists()

    log.record({"event": "after-rotation"})
    rotated = path.with_suffix(path.suffix + ".1")
    assert rotated.exists()
    assert [event["event"] for event in log.tail(5)] == ["after-rotation"]
    assert json.loads(rotated.read_text(encoding="utf-8").splitlines()[0])["event"] == "big"


def test_unwritable_path_does_not_raise(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    log = AuditLog(blocked / "nested" / "audit.jsonl")
    log.record({"event": "still-fine"})
    assert log.tail(5) == []
