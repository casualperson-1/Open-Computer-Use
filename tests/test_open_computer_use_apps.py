"""App catalog and default-app picker tests (no real apps are launched)."""

import pytest

from agent.computer import apps as app_catalog
from agent.computer.actions import ComputerToolbox
from agent.computer.base import ComputerError
from agent.computer.browser import normalise_address
from agent.computer.simulated import SimulatedComputer
from agent.providers.base import ToolCall


def test_parse_desktop_entry_and_kind_helpers(tmp_path):
    entry = tmp_path / "test.editor.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Test Editor\n"
        "Exec=test-editor %f\n"
        "MimeType=text/plain;application/pdf;\n"
        "Categories=Utility;\n"
    )
    app = app_catalog.parse_desktop_entry(entry)
    assert app is not None
    assert app.id == "test.editor"
    assert app.name == "Test Editor"
    assert app.mime_types == ["text/plain", "application/pdf"]

    hidden = tmp_path / "hidden.desktop"
    hidden.write_text("[Desktop Entry]\nType=Application\nName=Hidden\nExec=hidden\nNoDisplay=true\n")
    assert app_catalog.parse_desktop_entry(hidden) is None

    assert app_catalog.kind_for_target("/tmp/report.pdf") == ".pdf"
    assert app_catalog.kind_for_target("https://example.com") == "url"
    assert app_catalog.kind_label(".pdf") == "PDF files"
    assert app_catalog.normalise_kind("pdf") == ".pdf"
    assert app_catalog.normalise_kind("url") == "url"
    assert app_catalog.is_url("example.com") is True
    assert app_catalog.is_url("report.pdf") is False
    assert app_catalog.build_command("test-editor %f", "/tmp/a b.pdf").endswith("'/tmp/a b.pdf'")


class RecordingBrowser(SimulatedComputer):
    backend = "browser"

    def __init__(self):
        super().__init__()
        self.opened = []

    async def navigate(self, url):
        self.opened.append(url)


async def test_open_item_asks_once_then_remembers_the_app(monkeypatch):
    fake_apps = [
        app_catalog.InstalledApp(
            id="test.editor",
            name="Test Editor",
            command="test-editor %f",
            mime_types=["application/pdf"],
        )
    ]
    monkeypatch.setattr(app_catalog, "installed_apps", lambda: fake_apps)

    computer = SimulatedComputer()
    saved = {}
    asked = []

    async def save_default_app(kind, data):
        saved[kind] = data

    async def request_app_choice(request):
        asked.append(request)
        return {
            "app_id": "test.editor",
            "app_name": "Test Editor",
            "command": "test-editor %f",
            "always": True,
        }

    toolbox = ComputerToolbox(
        computer,
        default_apps={},
        request_app_choice=request_app_choice,
        save_default_app=save_default_app,
    )
    frame = await computer.capture()

    text, error = await toolbox.execute(
        ToolCall(id="1", name="open_item", arguments={"target": "/tmp/report.pdf"}), frame
    )
    assert error is False, text
    assert "Test Editor" in text
    assert asked[0]["kind"] == ".pdf"
    assert asked[0]["label"] == "PDF files"
    assert saved[".pdf"]["app_id"] == "test.editor"

    text, error = await toolbox.execute(
        ToolCall(id="2", name="open_item", arguments={"target": "/tmp/other.pdf"}), frame
    )
    assert error is False, text
    assert "default" in text
    assert len(asked) == 1  # the saved default skipped the picker


async def test_open_item_cancel_is_reported():
    computer = SimulatedComputer()

    async def request_app_choice(_request):
        return None

    toolbox = ComputerToolbox(computer, request_app_choice=request_app_choice)
    frame = await computer.capture()
    text, error = await toolbox.execute(
        ToolCall(id="1", name="open_item", arguments={"target": "/tmp/report.pdf"}), frame
    )
    assert error is True
    assert "cancelled" in text


async def test_browser_mode_opens_links_in_the_tab():
    computer = RecordingBrowser()
    toolbox = ComputerToolbox(computer, browser_mode=True)
    frame = await computer.capture()

    text, error = await toolbox.execute(
        ToolCall(id="1", name="open_item", arguments={"target": "example.com"}), frame
    )
    assert error is False, text
    assert computer.opened == ["https://example.com"]

    text, error = await toolbox.execute(
        ToolCall(id="2", name="open_item", arguments={"target": "weather tomorrow"}), frame
    )
    assert error is False, text
    assert computer.opened[-1].startswith("https://duckduckgo.com/?q=")

    text, error = await toolbox.execute(
        ToolCall(id="3", name="open_item", arguments={"target": "/tmp/report.pdf"}), frame
    )
    assert error is False
    assert computer.opened[-1].startswith("https://duckduckgo.com/?q=")


def test_browser_addresses_are_normalised():
    assert app_catalog.is_url("localhost:3000") is True
    assert app_catalog.is_url("127.0.0.1:8765/dashboard") is True
    assert app_catalog.is_url("report.pdf") is False
    assert normalise_address("example.com") == "https://example.com"
    assert normalise_address("https://example.com/x") == "https://example.com/x"
    assert normalise_address("weather in Paris").startswith("https://duckduckgo.com/?q=")
    with pytest.raises(ComputerError):
        normalise_address("   ")


async def test_navigate_tool_opens_websites_in_browser_mode():
    computer = RecordingBrowser()
    toolbox = ComputerToolbox(computer, browser_mode=True)
    frame = await computer.capture()
    assert "navigate" in [tool.name for tool in toolbox.tools]

    text, error = await toolbox.execute(
        ToolCall(id="1", name="navigate", arguments={"target": "example.com"}), frame
    )
    assert error is False, text
    assert computer.opened == ["https://example.com"]

    text, error = await toolbox.execute(
        ToolCall(id="2", name="navigate", arguments={"target": "weather tomorrow"}), frame
    )
    assert error is False, text
    assert computer.opened[-1].startswith("https://duckduckgo.com/?q=")


async def test_navigate_is_not_offered_in_desktop_mode():
    computer = SimulatedComputer()
    toolbox = ComputerToolbox(computer)
    frame = await computer.capture()
    assert "navigate" not in [tool.name for tool in toolbox.tools]
    text, error = await toolbox.execute(
        ToolCall(id="1", name="navigate", arguments={"target": "example.com"}), frame
    )
    assert error is True
    assert "Browser mode" in text
