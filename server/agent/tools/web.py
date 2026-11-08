"""Keyless web tools used by Chat mode."""

from __future__ import annotations

import html as html_module
import re
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

import httpx

from ..providers.base import ProviderError, ToolSpec

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

WEB_SEARCH_TOOL = ToolSpec(
    name="web_search",
    description=(
        "Search the public web and return result titles, links and snippets. Use this for "
        "current facts, news, prices or anything you are unsure about."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Number of results to return (default 5).",
            },
        },
        "required": ["query"],
    },
)

FETCH_PAGE_TOOL = ToolSpec(
    name="fetch_page",
    description=(
        "Download a web page and return its readable text. Use it after web_search to read a "
        "source in full before citing it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http:// or https:// page to read."},
        },
        "required": ["url"],
    },
)

_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.S | re.I)
_CLASS_RE = re.compile(r'class="([^"]*)"', re.I)
_HREF_RE = re.compile(r'href="([^"]*)"', re.I)
_SNIPPET_RE = re.compile(
    r'<(?P<tag>a|td|div|span)\b[^>]*class="[^"]*result__snippet[^"]*"[^>]*>'
    r"(?P<body>.*?)</(?P=tag)>",
    re.S | re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


def _clean(markup: str) -> str:
    return " ".join(html_module.unescape(_TAG_RE.sub(" ", markup or "")).split())


def _decode_url(value: str) -> str:
    value = html_module.unescape(value or "").strip()
    if value.startswith("//"):
        value = "https:" + value
    if "duckduckgo.com/l/" in value:
        target = parse_qs(urlparse(value).query).get("uddg", [""])[0]
        if target:
            return target
    return value


def parse_search_html(markup: str, limit: int = 6) -> List[Dict[str, str]]:
    """Parse DuckDuckGo's HTML results page into title/url/snippet entries."""
    results: List[Dict[str, str]] = []
    snippets = [_clean(match.group("body")) for match in _SNIPPET_RE.finditer(markup or "")]
    for match in _ANCHOR_RE.finditer(markup or ""):
        attrs = match.group("attrs")
        classes = " ".join(_CLASS_RE.findall(attrs)).lower()
        if "result__a" not in classes:
            continue
        href = _HREF_RE.search(attrs)
        if href is None:
            continue
        title = _clean(match.group("body"))
        if not title:
            continue
        index = len(results)
        results.append(
            {
                "title": title,
                "url": _decode_url(href.group(1)),
                "snippet": snippets[index][:300] if index < len(snippets) else "",
            }
        )
        if len(results) >= limit:
            break
    return results


async def search_web(query: str, limit: int = 5) -> List[Dict[str, str]]:
    query = (query or "").strip()
    if not query:
        raise ProviderError("A search query is required.")
    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await client.get(SEARCH_URL, params={"q": query})
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach the search engine: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderError(f"Search engine returned HTTP {response.status_code}.")
    return parse_search_html(response.text, max(1, min(10, limit)))


async def fetch_page(url: str, max_chars: int = 6000) -> Dict[str, str]:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ProviderError("Only http:// and https:// pages can be opened.")
    try:
        async with httpx.AsyncClient(
            timeout=25.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not open {url}: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderError(f"{url} returned HTTP {response.status_code}.")
    content_type = response.headers.get("content-type", "")
    if content_type and not any(
        hint in content_type for hint in ("text/", "html", "json", "xml")
    ):
        raise ProviderError(f"{url} is not a readable web page ({content_type}).")

    markup = response.text
    title_match = _TITLE_RE.search(markup)
    title = _clean(title_match.group(1)) if title_match else str(response.url)
    text = _SCRIPT_RE.sub(" ", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>", "\n", text)
    text = html_module.unescape(_TAG_RE.sub(" ", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return {
        "title": title or str(response.url),
        "url": str(response.url),
        "text": text[: max(500, max_chars)],
    }


__all__ = [
    "WEB_SEARCH_TOOL",
    "FETCH_PAGE_TOOL",
    "fetch_page",
    "parse_search_html",
    "search_web",
]
