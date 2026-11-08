"""Chat mode: keyless search parsing, artifact validation and the tool loop."""

import asyncio

import pytest

from agent.artifacts import normalize_artifact
from agent.open_computer_use.chat import ChatAgent
from agent.providers.mock import ScriptedProvider
from agent.tools.web import parse_search_html


def test_parse_search_html_decodes_duckduckgo_links():
    markup = """
    <div class="result results_links">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpython&amp;rut=abc">
        Python 3.14 <b>released</b>
      </a>
      <a class="result__snippet" href="#">Faster <b>startup</b> and a new REPL.</a>
    </div>
    <div class="result results_links">
      <a class="result__a" href="https://docs.python.org/3/whatsnew/3.14.html">What's new</a>
      <td class="result__snippet">The official changelog.</td>
    </div>
    """
    results = parse_search_html(markup, limit=5)
    assert len(results) == 2
    assert results[0]["title"] == "Python 3.14 released"
    assert results[0]["url"] == "https://example.com/python"
    assert results[0]["snippet"] == "Faster startup and a new REPL."
    assert results[1]["url"] == "https://docs.python.org/3/whatsnew/3.14.html"
    assert results[1]["snippet"] == "The official changelog."


def test_normalize_artifact_validates_kind_and_content():
    doc = normalize_artifact("document", "Report", "# Findings\n\nAll good.")
    assert doc == {
        "kind": "doc",
        "title": "Report",
        "content": "# Findings\n\nAll good.",
        "language": "",
    }

    deck = normalize_artifact("presentation", "", "# One\n\n---\n\n# Two", "")
    assert deck["kind"] == "slides"
    assert deck["title"] == "Untitled presentation"

    code = normalize_artifact("code", "rename.py", "print('hi')")
    assert code["kind"] == "code"
    assert code["language"] == "text"

    with pytest.raises(ValueError):
        normalize_artifact("spreadsheet", "Q3", "a,b")
    with pytest.raises(ValueError):
        normalize_artifact("doc", "Empty", "   ")


def test_chat_agent_searches_and_creates_an_artifact():
    saved = []
    events = []
    searches = []

    async def fake_search(query, limit):
        searches.append((query, limit))
        return [
            {
                "title": "Python 3.14 released",
                "url": "https://example.com/python",
                "snippet": "Faster startup and a new REPL.",
            }
        ]

    async def fake_save(**kwargs):
        saved.append(kwargs)
        return {
            "id": "artifact-1",
            "kind": kwargs["kind"],
            "title": kwargs["title"],
            "content": kwargs["content"],
            "language": kwargs.get("language") or "",
        }

    provider = ScriptedProvider(
        [
            {
                "text": "Let me check the latest release.",
                "tool_calls": [("web_search", {"query": "python 3.14 release", "limit": 3})],
            },
            {
                "tool_calls": [
                    (
                        "create_artifact",
                        {
                            "kind": "slides",
                            "title": "Python 3.14",
                            "content": "# Python 3.14\n\nWhat's new\n\n---\n\n## Faster startup\n\n- New REPL",
                        },
                    )
                ]
            },
            {"text": "Your deck is ready."},
        ]
    )

    async def collect(event):
        events.append(event)

    agent = ChatAgent(
        provider=provider,
        on_event=collect,
        save_artifact=fake_save,
        search=fake_search,
        history=[{"role": "user", "content": "We were talking about Python."}],
    )
    result = asyncio.run(agent.run("Make a slide deck about Python 3.14"))

    assert result.status == "completed"
    assert result.summary == "Your deck is ready."
    assert result.steps == 2
    assert searches == [("python 3.14 release", 3)]
    assert saved[0]["kind"] == "slides"
    assert saved[0]["title"] == "Python 3.14"
    assert "---" in saved[0]["content"]

    types = [event["type"] for event in events]
    assert "assistant" in types
    assert "action" in types
    assert "observation" in types
    assert "artifact" in types
    artifact_event = next(event for event in events if event["type"] == "artifact")
    assert artifact_event["artifact"]["id"] == "artifact-1"

    tool_names = {spec.name for spec in provider.request_tools[0]}
    assert {"web_search", "fetch_page", "create_artifact"} <= tool_names
    assert provider.requests[0][1].content == "We were talking about Python."
    tool_messages = [msg for msg in provider.requests[1] if msg.role == "tool"]
    assert "https://example.com/python" in tool_messages[-1].content
    assert "Unknown tool" not in tool_messages[-1].content


def test_chat_agent_answers_without_tools():
    provider = ScriptedProvider([{"text": "Paris is the capital of France."}])
    agent = ChatAgent(provider)
    result = asyncio.run(agent.run("What is the capital of France?"))

    assert result.status == "completed"
    assert result.summary == "Paris is the capital of France."
    assert result.steps == 0
    assert provider.request_tools[0] is not None
