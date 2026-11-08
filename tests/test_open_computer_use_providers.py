"""Provider serialization and parsing tests (no network calls)."""

import json

from agent.providers.anthropic import AnthropicProvider
from agent.providers.base import ChatMessage, ToolCall, ToolSpec
from agent.providers.deepseek import DeepSeekProvider
from agent.providers.factory import available_providers, get_provider, normalise_provider
from agent.providers.gemini import GeminiProvider
from agent.providers.hosted import GroqProvider, OpenRouterProvider, XAIProvider
from agent.providers.ollama import OllamaProvider, parse_library_html
from agent.providers.openai_compat import OpenAICompatibleProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 24
CLICK_TOOL = ToolSpec(
    name="click",
    description="Click the screen",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "number", "description": "x"},
            "y": {"type": "number", "description": "y"},
        },
        "required": ["x", "y"],
        "additionalProperties": False,
    },
)


def conversation():
    return [
        ChatMessage(role="system", content="You are Open Computer Use."),
        ChatMessage(role="user", content="Open the folder", images=[PNG]),
        ChatMessage(
            role="assistant",
            content="Clicking.",
            tool_calls=[ToolCall(id="c1", name="click", arguments={"x": 12, "y": 34})],
        ),
        ChatMessage(role="tool", content="Clicked at 12,34.", tool_call_id="c1", name="click"),
        ChatMessage(role="user", content="Next screenshot.", images=[PNG]),
    ]


def test_openai_compatible_payload_and_tool_parsing():
    provider = OpenAICompatibleProvider(api_key="sk-test", model="gpt-4o")
    payload = provider.build_payload(conversation(), [CLICK_TOOL])

    assert payload["model"] == "gpt-4o"
    assert payload["tools"][0]["function"]["name"] == "click"
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["content"][1]["type"] == "image_url"
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert messages[2]["tool_calls"][0]["function"]["name"] == "click"
    assert json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]) == {"x": 12, "y": 34}
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "Clicked at 12,34."}

    turn = provider.parse_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "Done thinking",
                        "tool_calls": [
                            {
                                "id": "call_9",
                                "type": "function",
                                "function": {"name": "click", "arguments": '{"x": 5, "y": 6}'},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
    )
    assert turn.text == "Done thinking"
    assert turn.tool_calls[0].arguments == {"x": 5, "y": 6}
    assert turn.tokens_in == 100 and turn.tokens_out == 20
    assert turn.estimated_cost > 0


def test_anthropic_payload_merges_tool_results_and_vision():
    provider = AnthropicProvider(api_key="sk-ant-test", model="claude-sonnet-4-5")
    payload = provider.build_payload(conversation(), [CLICK_TOOL])

    assert payload["system"] == "You are Open Computer Use."
    assert payload["tools"][0]["input_schema"]["required"] == ["x", "y"]
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["content"][0]["type"] == "image"
    assert payload["messages"][1]["content"][1]["type"] == "tool_use"
    # Tool results are folded into the same user turn as the next screenshot.
    last = payload["messages"][-1]
    assert last["role"] == "user"
    types = [block["type"] for block in last["content"]]
    assert types == ["tool_result", "image", "text"]

    turn = provider.parse_response(
        {
            "content": [
                {"type": "text", "text": "Let's click."},
                {"type": "tool_use", "id": "toolu_1", "name": "click", "input": {"x": 1, "y": 2}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 10},
        }
    )
    assert turn.text == "Let's click."
    assert turn.tool_calls[0].id == "toolu_1"


def test_gemini_payload_uses_inline_data_and_uppercase_schema():
    provider = GeminiProvider(api_key="test", model="gemini-2.5-flash")
    payload = provider.build_payload(conversation(), [CLICK_TOOL])

    assert payload["systemInstruction"]["parts"][0]["text"] == "You are Open Computer Use."
    user_parts = payload["contents"][0]["parts"]
    assert user_parts[0]["inlineData"]["mimeType"] == "image/png"
    declaration = payload["tools"][0]["functionDeclarations"][0]
    assert declaration["parameters"]["type"] == "OBJECT"
    assert declaration["parameters"]["properties"]["x"]["type"] == "NUMBER"
    assert "additionalProperties" not in declaration["parameters"]
    tool_turns = [
        content for content in payload["contents"]
        if "functionResponse" in content["parts"][0]
    ]
    assert tool_turns[0]["parts"][0]["functionResponse"]["name"] == "click"
    roles = [content["role"] for content in payload["contents"]]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles

    turn = provider.parse_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Clicking there."},
                            {"functionCall": {"name": "click", "args": {"x": 7, "y": 8}}},
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 9},
        }
    )
    assert turn.tool_calls[0].arguments == {"x": 7, "y": 8}
    assert turn.estimated_cost >= 0


def test_gemini_round_trips_thought_signature_on_tool_calls():
    provider = GeminiProvider(api_key="test", model="gemini-3-flash-preview")
    turn = provider.parse_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "press_key",
                                    "args": {"key": "space"},
                                },
                                "thoughtSignature": "c2lnbmF0dXJl",
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert turn.tool_calls[0].thought_signature == "c2lnbmF0dXJl"

    messages = [
        ChatMessage(role="user", content="Press space"),
        ChatMessage(role="assistant", tool_calls=turn.tool_calls),
        ChatMessage(role="tool", content="Pressed space.", name="press_key"),
    ]
    payload = provider.build_payload(messages, [CLICK_TOOL])
    call_part = next(
        part
        for part in payload["contents"][1]["parts"]
        if "functionCall" in part
    )
    assert call_part["thoughtSignature"] == "c2lnbmF0dXJl"


def test_ollama_payload_and_tool_parsing():
    provider = OllamaProvider(model="qwen2.5vl:7b", base_url="http://localhost:11434")
    assert provider.get_capabilities().has_vision is True
    payload = provider.build_payload(conversation(), [CLICK_TOOL])

    assert payload["messages"][1]["images"]
    assert payload["tools"][0]["function"]["name"] == "click"
    assert payload["messages"][2]["tool_calls"][0]["function"]["arguments"] == {"x": 12, "y": 34}

    turn = provider.parse_response(
        {
            "message": {
                "content": "On it",
                "tool_calls": [{"function": {"name": "click", "arguments": {"x": 3, "y": 4}}}],
            },
            "prompt_eval_count": 12,
            "eval_count": 3,
        }
    )
    assert turn.tool_calls[0].name == "click"
    assert turn.tool_calls[0].arguments == {"x": 3, "y": 4}


def test_factory_aliases_and_catalog():
    assert normalise_provider("claude") == "anthropic"
    assert normalise_provider("openrouter") == "openrouter"
    assert normalise_provider("grok") == "xai"
    assert normalise_provider("x-ai") == "xai"
    assert normalise_provider("nim") == "nvidia"
    assert normalise_provider("LOCAL") == "ollama"

    provider = get_provider("custom", api_key="k", base_url="http://localhost:1234/v1", model="local-model")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:1234/v1"

    catalog = {entry["id"]: entry for entry in available_providers()}
    assert set(catalog) == {
        "anthropic", "openai", "gemini", "deepseek", "xai", "openrouter", "groq",
        "nvidia", "mistral", "together", "fireworks", "cerebras", "perplexity",
        "ollama", "openai-compatible",
    }
    assert catalog["deepseek"]["has_vision"] is False
    assert catalog["openai-compatible"]["needs_base_url"] is True
    assert isinstance(get_provider("deepseek", api_key="x"), DeepSeekProvider)


def test_hosted_provider_defaults_and_vision_hints():
    assert isinstance(get_provider("groq", api_key="x"), GroqProvider)
    assert isinstance(get_provider("openrouter", api_key="x"), OpenRouterProvider)
    assert isinstance(get_provider("grok", api_key="x"), XAIProvider)

    catalog = {entry["id"]: entry for entry in available_providers()}
    for provider_id in (
        "openrouter", "groq", "nvidia", "xai", "mistral",
        "together", "fireworks", "cerebras", "perplexity",
    ):
        assert catalog[provider_id]["needs_key"] is True
        assert catalog[provider_id]["has_vision"] in (True, False)
    assert catalog["nvidia"]["label"] == "NVIDIA NIM"
    assert catalog["xai"]["label"] == "xAI (Grok)"
    assert catalog["perplexity"]["has_vision"] is False

    assert get_provider("openrouter", api_key="x").base_url == "https://openrouter.ai/api/v1"
    assert get_provider("groq", api_key="x").default_model.startswith("meta-llama/")
    # Vision is inferred from the selected model for mixed text/vision providers.
    assert get_provider("groq", api_key="x").has_vision is True
    assert get_provider("groq", api_key="x", model="llama-3.3-70b-versatile").has_vision is False
    assert get_provider("nvidia", api_key="x", model="meta/llama-3.1-8b-instruct").has_vision is False
    assert get_provider("perplexity", api_key="x").model == "sonar"


def test_ollama_library_html_is_parsed():
    html = '''
    <ul>
      <li><a href="/library/llama3.2" class="group">
        <h2><span>llama3.2</span></h2>
        <p>Meta's Llama 3.2 goes small.</p>
      </a></li>
      <li><a href="/library/qwen3-vl"><h2>qwen3-vl</h2><p>Vision and text.</p></a></li>
      <li><a href="/library/llama3.2"><h2>duplicate</h2></a></li>
    </ul>
    '''
    results = parse_library_html(html)
    assert [item["name"] for item in results] == ["llama3.2", "qwen3-vl"]
    assert results[0]["description"].startswith("Meta's Llama 3.2")
    assert parse_library_html("") == []
