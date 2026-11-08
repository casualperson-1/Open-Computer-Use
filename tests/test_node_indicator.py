"""The on-screen indicator: state mapping, safe opt-out, notification fallback."""

from node.indicator import ScreenIndicator, indicator_state


def test_state_reflects_stop_pause_connection_and_idle():
    stopped = indicator_state({"connected": True, "stopped": True, "backend": "desktop"})
    assert stopped["level"] == "stopped"
    assert "restart" in stopped["detail"]

    paused = indicator_state(
        {"connected": True, "paused": True, "safety": {"reason": "Paused locally."}}
    )
    assert paused["level"] == "paused"
    assert paused["detail"] == "Paused locally."

    connected = indicator_state({"connected": True, "backend": "browser"})
    assert connected["level"] == "connected"
    assert connected["detail"] == "browser"

    idle = indicator_state({"connected": False})
    assert idle["level"] == "idle"


def test_indicator_can_be_disabled_and_never_raises():
    indicator = ScreenIndicator(lambda: {"connected": True}, enabled=False)
    assert indicator.start() is False
    indicator.stop()

    # Notifications are best effort: no notify-send, no crash.
    quiet = ScreenIndicator(lambda: {}, notify=True)
    quiet._notify("hello")
    quiet._on_change({"level": "connected"})
    quiet._on_change({"level": "connected"})
