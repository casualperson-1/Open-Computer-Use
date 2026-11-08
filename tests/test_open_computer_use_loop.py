"""Agent loop tests with a scripted model and the simulated desktop."""

from agent.computer.actions import ComputerToolbox
from agent.computer.simulated import SimulatedComputer
from agent.open_computer_use.loop import OpenComputerUseAgent, RunControl
from agent.providers.mock import ScriptedProvider


def make_agent(script, computer=None, **kwargs):
    provider = kwargs.pop("provider", None) or ScriptedProvider(script)
    events = []

    async def on_event(event):
        events.append(event)

    computer = computer or SimulatedComputer()
    agent = OpenComputerUseAgent(
        provider=provider,
        toolbox=ComputerToolbox(computer),
        on_event=on_event,
        max_steps=kwargs.pop("max_steps", 6),
        max_cost=kwargs.pop("max_cost", 1.0),
        **kwargs,
    )
    return agent, provider, computer, events


async def test_loop_executes_action_then_finishes():
    agent, provider, computer, events = make_agent(
        [
            {"text": "Clicking the folder.", "tool_calls": [("click", {"x": 200, "y": 180})]},
            {"tool_calls": [("done", {"summary": "Opened Documents."})]},
        ]
    )

    result = await agent.run("Open the Documents folder")

    assert result.status == "completed"
    assert result.summary == "Opened Documents."
    assert result.steps == 1
    assert computer.events[-1]["action"] == "click"
    assert [event["type"] for event in events].count("screenshot") >= 2
    assert any(event["type"] == "action" and event["tool"] == "click" for event in events)
    assert any(event["type"] == "observation" and event["ok"] for event in events)

    # The second request must contain the tool result and a fresh screenshot.
    second_request = provider.requests[-1]
    assert any(message.role == "tool" and message.name == "click" for message in second_request)
    assert second_request[-1].images, "the newest observation should carry a screenshot"
    # Compaction keeps only the newest screenshot.
    older_users = [m for m in second_request if m.role == "user" and m.images]
    assert len(older_users) == 1


async def test_ask_user_pauses_and_resumes_with_reply():
    asked = []

    async def ask_user(question):
        asked.append(question)
        return "yes, go ahead"

    agent, provider, computer, events = make_agent(
        [
            {"tool_calls": [("ask_user", {"question": "May I overwrite the file?"})]},
            {"tool_calls": [("done", {"summary": "Overwrote the file."})]},
        ],
        ask_user=ask_user,
    )

    result = await agent.run("Overwrite report.txt")

    assert result.status == "completed"
    assert asked == ["May I overwrite the file?"]
    assert any(event["type"] == "user_reply" for event in events)
    assert any("yes, go ahead" in message.content for message in provider.requests[-1])


async def test_denied_approval_is_reported_back_to_the_model():
    script_calls = []

    async def request_approval(call):
        script_calls.append(call.name)
        return False

    agent, provider, computer, events = make_agent(
        [
            {"tool_calls": [("click", {"x": 10, "y": 10})]},
            {"tool_calls": [("done", {"summary": "Stopped without clicking."})]},
        ],
        request_approval=request_approval,
    )

    result = await agent.run("Do not actually click")

    assert result.status == "completed"
    assert script_calls == ["click"]
    assert computer.events == []  # no input was generated
    assert any(event["type"] == "observation" and not event["ok"] for event in events)
    assert any(message.is_error and message.role == "tool" for message in provider.requests[-1])


async def test_stop_before_start_returns_quickly():
    agent, provider, computer, events = make_agent([{"tool_calls": [("done", {"summary": "x"})]}])
    agent.control.stop()
    result = await agent.run("anything")
    assert result.status == "stopped"
    assert provider.requests == []


async def test_step_limit_and_text_only_models():
    agent, provider, computer, events = make_agent(
        [{"tool_calls": [("click", {"x": 10, "y": 10})]}] * 4,
        max_steps=1,
    )
    provider.has_vision = False

    result = await agent.run("Click once")

    assert result.status == "max_steps"
    assert all(not message.images for message in provider.requests[0])


async def test_pause_event_blocks_until_resumed():
    import asyncio

    agent, provider, computer, events = make_agent(
        [{"tool_calls": [("done", {"summary": "done"})]}]
    )
    agent.control.pause()
    task = asyncio.create_task(agent.run("Pause test"))
    await asyncio.sleep(0.15)
    assert not task.done()
    agent.control.resume()
    result = await asyncio.wait_for(task, timeout=3)
    assert result.status == "completed"
