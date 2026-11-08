"""Chat mode: a normal assistant with web search, Canvas and MCP tools."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..artifacts import ARTIFACT_TOOL
from ..providers.base import ChatMessage, ModelProvider, ToolCall, ToolSpec
from ..tools.web import FETCH_PAGE_TOOL, WEB_SEARCH_TOOL, fetch_page, search_web
from .loop import EventCallback, MAX_TOOL_CALLS_PER_TURN, RunControl, RunResult

logger = logging.getLogger(__name__)

ArtifactSaver = Callable[..., Awaitable[Dict[str, Any]]]
SearchFn = Callable[[str, int], Awaitable[List[Dict[str, str]]]]
FetchFn = Callable[[str], Awaitable[Dict[str, str]]]

CHAT_PROMPT = """You are Open Computer Use in Chat mode: a helpful, direct general-purpose assistant.

ENVIRONMENT
- You cannot see or control the user's desktop in this mode. You have no mouse, keyboard or screenshots.
- Today is {today}.
- {memory}

TOOLS
- web_search: search the public web. Use it for current facts, news, prices, releases, or anything you are unsure about.
- fetch_page: read a page in full. Prefer reading the source before quoting precise details.
- create_artifact: put a polished document, slide deck or code file on the user's Canvas. Use it whenever the user asks you to write, draft, make a presentation, or produce code they will keep.
- Never claim you searched or opened something unless you called the corresponding tool.
- When you use web results, cite the source links inline.

STYLE
- Answer the question first, then add the detail that matters. Be concise but complete.
- Use markdown for structure: short paragraphs, bullet lists, tables when they help.
- If the user asks for slides, write create_artifact with kind="slides": each slide is markdown, separated by a line containing only ---. Put the slide title first on each slide.
- If the user asks for a document/report, write create_artifact with kind="doc" and full markdown.
- If the user asks for code, write create_artifact with kind="code", the language, and the complete source. You can also show the code in your reply.
- Ask a short clarifying question only when the request is genuinely ambiguous; otherwise make a reasonable choice and say what you assumed.
"""


class ChatAgent:
    def __init__(
        self,
        provider: ModelProvider,
        control: Optional[RunControl] = None,
        max_steps: int = 12,
        max_cost: float = 1.0,
        on_event: Optional[EventCallback] = None,
        memory: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        extra_tools: Optional[List[ToolSpec]] = None,
        extra_executor: Optional[Callable[[ToolCall], Awaitable[tuple[str, bool]]]] = None,
        save_artifact: Optional[ArtifactSaver] = None,
        search: SearchFn = search_web,
        fetch: FetchFn = fetch_page,
    ) -> None:
        self.provider = provider
        self.control = control or RunControl()
        self.max_steps = max_steps
        self.max_cost = max_cost
        self.on_event = on_event
        self.memory = (memory or "").strip() or "No notes saved."
        self.history = list(history or [])
        self.extra_executor = extra_executor
        self.extra_names = {spec.name for spec in (extra_tools or [])}
        self.save_artifact = save_artifact
        self.search = search
        self.fetch = fetch

        specs: List[ToolSpec] = [WEB_SEARCH_TOOL, FETCH_PAGE_TOOL]
        if save_artifact is not None:
            specs.append(ARTIFACT_TOOL)
        names = {spec.name for spec in specs}
        for spec in extra_tools or []:
            if spec.name not in names:
                specs.append(spec)
                names.add(spec.name)
        self.tools = specs

        self.steps = 0
        self.cost = 0.0
        self.tokens_in = 0
        self.tokens_out = 0

    async def _emit(self, event: Dict[str, Any]) -> None:
        if not self.on_event:
            return
        try:
            await self.on_event(event)
        except Exception:  # pragma: no cover - UI callback should never kill a run
            logger.exception("Event callback failed")

    def _system_prompt(self) -> str:
        from datetime import date

        return CHAT_PROMPT.format(today=date.today().isoformat(), memory=self.memory)

    async def _execute_tool(self, call: ToolCall) -> tuple[str, bool]:
        name = call.name
        args: Dict[str, Any] = call.arguments or {}
        try:
            if name == "web_search":
                query = str(args.get("query") or "").strip()
                if not query:
                    return "query is required.", True
                try:
                    limit = max(1, min(10, int(args.get("limit") or 5)))
                except (TypeError, ValueError):
                    limit = 5
                results = await self.search(query, limit)
                if not results:
                    return f"No web results for “{query}”.", False
                lines = [f"Web results for “{query}”:"]
                for index, item in enumerate(results, 1):
                    lines.append(
                        f"{index}. {item.get('title') or 'Untitled'}\n"
                        f"   {item.get('url') or ''}\n"
                        f"   {item.get('snippet') or ''}"
                    )
                return "\n".join(lines), False

            if name == "fetch_page":
                url = str(args.get("url") or "").strip()
                if not url:
                    return "url is required.", True
                page = await self.fetch(url)
                return (
                    f"{page.get('title') or url}\n{page.get('url') or url}\n\n"
                    f"{page.get('text') or ''}"
                ), False

            if name == "create_artifact":
                if self.save_artifact is None:
                    return "Canvas is not available in this run.", True
                artifact = await self.save_artifact(
                    kind=str(args.get("kind") or "doc"),
                    title=str(args.get("title") or ""),
                    content=str(args.get("content") or ""),
                    language=str(args.get("language") or ""),
                )
                await self._emit({"type": "artifact", "artifact": artifact})
                return f"Created “{artifact.get('title')}” and opened it in Canvas.", False

            if self.extra_executor is not None and name in self.extra_names:
                return await self.extra_executor(call)

            return f"Unknown tool '{name}'.", True
        except ValueError as exc:
            return str(exc), True
        except Exception as exc:
            return f"'{name}' failed: {exc}", True

    async def run(self, goal: str) -> RunResult:
        self.steps = 0
        self.cost = 0.0
        self.tokens_in = 0
        self.tokens_out = 0

        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=self._system_prompt())
        ]
        for item in self.history[-12:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append(ChatMessage(role=role, content=content[:4000]))
        messages.append(ChatMessage(role="user", content=goal))

        await self._emit(
            {"type": "status", "status": "running", "step": 0, "cost": 0.0}
        )
        last_text = ""
        status = "max_steps"

        while self.steps < self.max_steps:
            if not await self.control.checkpoint():
                return self._result("stopped", "Stopped by the user.")
            if self.cost >= self.max_cost:
                return self._result(
                    "limit_reached",
                    f"Cost limit reached (${self.cost:.4f} of ${self.max_cost:.2f}).",
                )

            try:
                turn = await self.provider.generate(messages, self.tools)
            except Exception as exc:
                logger.exception("Chat provider call failed")
                await self._emit({"type": "error", "message": str(exc)})
                return self._result("error", error=str(exc))

            self.tokens_in += turn.tokens_in
            self.tokens_out += turn.tokens_out
            self.cost += turn.estimated_cost

            text = (turn.text or "").strip()
            if text:
                last_text = text
                await self._emit({"type": "assistant", "text": text})

            messages.append(
                ChatMessage(role="assistant", content=text, tool_calls=turn.tool_calls)
            )

            if not turn.tool_calls:
                return self._result("completed", last_text or "Done.")

            for index, call in enumerate(turn.tool_calls):
                if index >= MAX_TOOL_CALLS_PER_TURN:
                    messages.append(
                        ChatMessage(
                            role="tool",
                            tool_call_id=call.id,
                            name=call.name,
                            content="Skipped because this turn reached the tool-call limit.",
                        )
                    )
                    continue
                if not await self.control.checkpoint():
                    return self._result("stopped", "Stopped by the user.")

                await self._emit(
                    {
                        "type": "action",
                        "step": self.steps + 1,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "call_id": call.id,
                    }
                )
                result, is_error = await self._execute_tool(call)
                await self._emit(
                    {
                        "type": "observation",
                        "text": result,
                        "ok": not is_error,
                        "tool": call.name,
                    }
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=result,
                        is_error=is_error,
                    )
                )

            self.steps += 1
            await self._emit(
                {
                    "type": "status",
                    "status": "running",
                    "step": self.steps,
                    "cost": round(self.cost, 6),
                }
            )

        return self._result(
            status, last_text or f"Reached the {self.max_steps} step limit."
        )

    def _result(
        self, status: str, summary: str = "", error: Optional[str] = None
    ) -> RunResult:
        return RunResult(
            status=status,
            summary=summary,
            steps=self.steps,
            cost=round(self.cost, 6),
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            error=error,
        )


__all__ = ["ChatAgent", "CHAT_PROMPT"]
