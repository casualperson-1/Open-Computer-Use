"""Canvas artifacts: documents, slide decks and code snippets."""

from __future__ import annotations

from typing import Any, Dict

from .providers.base import ToolSpec

ARTIFACT_KINDS = ("doc", "slides", "code")
MAX_ARTIFACT_CHARS = 40000

ARTIFACT_TOOL = ToolSpec(
    name="create_artifact",
    description=(
        "Create a nicely formatted artifact in Canvas. Use this whenever the user asks for a "
        "document, report, slide deck/presentation or a code file. The artifact opens in the "
        "Canvas panel and is saved with the chat. For slides, content is markdown with slides "
        "separated by a line containing only ---. For code, content is the complete source."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["doc", "slides", "code"],
                "description": "doc = markdown document, slides = markdown deck, code = source file.",
            },
            "title": {"type": "string", "description": "Short title shown in Canvas."},
            "content": {
                "type": "string",
                "description": "Markdown document/deck, or the complete source code.",
            },
            "language": {
                "type": "string",
                "description": "Programming language for code artifacts, e.g. python.",
            },
        },
        "required": ["kind", "title", "content"],
    },
)


def normalize_artifact(
    kind: str, title: str, content: str, language: str = ""
) -> Dict[str, str]:
    """Validate and normalise artifact fields; raises ValueError with a readable reason."""
    aliases = {
        "document": "doc",
        "markdown": "doc",
        "report": "doc",
        "presentation": "slides",
        "deck": "slides",
        "slide": "slides",
        "snippet": "code",
        "source": "code",
    }
    resolved = (kind or "doc").strip().lower()
    resolved = aliases.get(resolved, resolved)
    if resolved not in ARTIFACT_KINDS:
        raise ValueError("kind must be doc, slides or code.")

    text = (content or "").strip()
    if not text:
        raise ValueError("content is required and cannot be empty.")
    if len(text) > MAX_ARTIFACT_CHARS:
        raise ValueError(f"content is too long (limit {MAX_ARTIFACT_CHARS} characters).")

    defaults = {
        "doc": "Untitled document",
        "slides": "Untitled presentation",
        "code": "Untitled snippet",
    }
    name = (title or "").strip()[:160] or defaults[resolved]
    lang = (language or "").strip().lower()[:40]
    if resolved == "code" and not lang:
        lang = "text"
    return {
        "kind": resolved,
        "title": name,
        "content": text,
        "language": lang if resolved == "code" else "",
    }


__all__ = ["ARTIFACT_KINDS", "ARTIFACT_TOOL", "MAX_ARTIFACT_CHARS", "normalize_artifact"]
