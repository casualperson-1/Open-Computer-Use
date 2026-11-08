"""Computer driver and tool-execution tests (no real input)."""

import io

from PIL import Image

from agent.computer.actions import ComputerToolbox
from agent.computer.base import Computer, Frame
from agent.computer.simulated import SimulatedComputer
from agent.providers.base import ToolCall


class FakeScaledComputer(Computer):
    backend = "fake"

    def __init__(self):
        self.calls = []

    async def capture(self) -> Frame:
        return Frame(png=b"", width=640, height=360, screen_width=1280, screen_height=720)

    async def click(self, x, y, button="left", clicks=1):
        self.calls.append(("click", x, y, button, clicks))

    async def move(self, x, y):
        self.calls.append(("move", x, y))

    async def drag(self, from_x, from_y, to_x, to_y):
        self.calls.append(("drag", from_x, from_y, to_x, to_y))

    async def scroll(self, x, y, amount):
        self.calls.append(("scroll", x, y, amount))

    async def type_text(self, text):
        self.calls.append(("type", text))

    async def press_key(self, keys):
        self.calls.append(("key", keys))


async def test_toolbox_maps_screenshot_coordinates_to_screen():
    computer = FakeScaledComputer()
    toolbox = ComputerToolbox(computer)
    frame = await toolbox.capture()

    text, error = await toolbox.execute(
        ToolCall(id="1", name="click", arguments={"x": 320, "y": 180}), frame
    )
    assert error is False
    assert "(320, 180)" in text
    assert computer.calls[0] == ("click", 640.0, 360.0, "left", 1)

    _, error = await toolbox.execute(
        ToolCall(id="2", name="click", arguments={"x": 900, "y": 10}), frame
    )
    assert error is True
    assert "outside the screenshot" in _


async def test_toolbox_rejects_bad_arguments_and_supports_keyboard():
    computer = FakeScaledComputer()
    toolbox = ComputerToolbox(computer)
    frame = await toolbox.capture()

    text, error = await toolbox.execute(ToolCall(id="1", name="click", arguments={"x": 5}), frame)
    assert error is True and "needs numeric x and y" in text

    text, error = await toolbox.execute(
        ToolCall(id="2", name="type_text", arguments={"text": "hello"}), frame
    )
    assert error is False
    assert computer.calls[-1] == ("type", "hello")

    await toolbox.execute(ToolCall(id="3", name="press_key", arguments={"keys": "ctrl+s"}), frame)
    assert computer.calls[-1] == ("key", "ctrl+s")

    text, error = await toolbox.execute(ToolCall(id="4", name="wat", arguments={}), frame)
    assert error is True and "Unknown tool" in text


async def test_simulated_desktop_renders_and_records_actions():
    computer = SimulatedComputer()
    frame = await computer.capture()
    image = Image.open(io.BytesIO(frame.png))
    assert image.size == (1280, 720)
    assert frame.screen_width == 1280 and frame.scale_x == 1.0

    await computer.click(100, 150)
    await computer.type_text("hello cowork")
    await computer.press_key("ctrl+l")

    assert computer.events[0]["action"] == "move"
    assert computer.events[1]["action"] == "click"
    assert computer.events[-2]["text"] == "hello cowork"
    assert computer.events[-1]["keys"] == "ctrl+l"
    assert "hello cowork" in computer.notes

    second = await computer.capture()
    assert second.png != frame.png
    assert second.jpeg_data_url().startswith("data:image/jpeg;base64,")

    info = computer.describe()
    assert info["backend"] == "simulated"
    assert info["real_input"] is False
