from __future__ import annotations

import asyncio
import importlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.config import WebPermissionConfig, WebToolsConfig


TavilyClientFactory = Callable[[str], Any]
DDGSFactory = Callable[[float], Any]

_DIRECT_GET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 XAgent/0.2"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_DIRECT_MIN_BYTES = 64 * 1024
_DIRECT_MAX_BYTES = 2 * 1024 * 1024


@tool(
    name="web_fetch",
    description="Fetch a URL and return clean model-readable page content.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
            "max_chars": {
                "type": ["integer", "null"],
                "description": "Maximum content characters to return.",
                "default": None,
            },
            "timeout_seconds": {
                "type": ["number", "null"],
                "description": "Request timeout in seconds.",
                "default": None,
            },
        },
        "required": ["url"],
    },
)
class WebFetchTool(Tool):
    def __init__(
        self,
        approver: Approver,
        config: WebToolsConfig,
        web_permission: WebPermissionConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.approver = approver
        self.config = config
        self.web_permission = web_permission or WebPermissionConfig()
        self.transport = transport

    async def execute(
        self,
        url: str,
        max_chars: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        url = url.strip()
        url_error = _validate_fetch_url(url)
        if url_error is not None:
            return ToolResult.fail(url_error)
        permission_error = await _check_web_permission(
            self.approver,
            self.web_permission,
            url,
            summary=f"web_fetch via {self.config.fetch_backend}",
        )
        if permission_error is not None:
            return permission_error
        if self.config.fetch_backend != "jina":
            return ToolResult.fail(f"Unsupported web fetch backend: {self.config.fetch_backend}")

        limit = max(max_chars if max_chars is not None else self.config.max_fetch_chars, 0)
        timeout = timeout_seconds if timeout_seconds is not None else self.config.timeout_seconds
        jina_error: Exception | None = None
        endpoint = _join_reader_url(self.config.jina.reader_base_url, url)
        headers = {"Accept": "application/json"}
        if self.config.jina.api_key:
            headers["Authorization"] = f"Bearer {self.config.jina.api_key}"

        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(endpoint, headers=headers)
                response.raise_for_status()
                title, source_url, content = _parse_jina_response(response, fallback_url=url)
                content, truncated = _truncate(content, limit)
                rendered = _format_fetch_result(
                    source_url=source_url,
                    title=title,
                    content=content,
                    truncated=truncated,
                    backend="jina",
                )
                return ToolResult.ok(
                    rendered,
                    data={
                        "backend": "jina",
                        "url": source_url,
                        "title": title,
                        "content": content,
                        "truncated": truncated,
                    },
                )
        except httpx.HTTPError as exc:
            jina_error = exc

        try:
            direct_response = await _fetch_direct_get(
                url,
                timeout=timeout,
                max_chars=limit,
                transport=self.transport,
            )
        except (httpx.HTTPError, DirectFetchError) as exc:
            return ToolResult.fail(
                f"web_fetch failed for {url}: "
                f"jina={_format_exception(jina_error)}; direct={_format_exception(exc)}"
            )

        title, source_url, content = _parse_direct_response(direct_response, fallback_url=url)
        content, content_truncated = _truncate(content, limit)
        truncated = content_truncated or direct_response.download_truncated
        rendered = _format_fetch_result(
            source_url=source_url,
            title=title,
            content=content,
            truncated=truncated,
            backend="direct",
            fallback_from="jina",
        )
        return ToolResult.ok(
            rendered,
            data={
                "backend": "direct",
                "fallback_from": "jina",
                "fallback_error": _format_exception(jina_error),
                "url": source_url,
                "title": title,
                "content": content,
                "truncated": truncated,
                "download_truncated": direct_response.download_truncated,
            },
        )


@tool(
    name="web_search",
    description="Search the web and return relevant result links with snippets.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {
                "type": ["integer", "null"],
                "description": "Maximum number of search results.",
                "default": None,
            },
        },
        "required": ["query"],
    },
)
class WebSearchTool(Tool):
    def __init__(
        self,
        approver: Approver,
        config: WebToolsConfig,
        web_permission: WebPermissionConfig | None = None,
        *,
        tavily_client_factory: TavilyClientFactory | None = None,
        ddgs_factory: DDGSFactory | None = None,
    ) -> None:
        self.approver = approver
        self.config = config
        self.web_permission = web_permission or WebPermissionConfig()
        self.tavily_client_factory = tavily_client_factory
        self.ddgs_factory = ddgs_factory

    async def execute(self, query: str, max_results: int | None = None) -> ToolResult:
        backend = self._resolve_backend()
        if backend == "unavailable":
            return ToolResult.fail("No web search backend is available.")
        permission_error = await _check_web_permission(
            self.approver,
            self.web_permission,
            f"web_search:{query[:200]}",
            summary=f"web_search via {backend}",
        )
        if permission_error is not None:
            return permission_error
        limit = max(max_results if max_results is not None else self.config.max_search_results, 0)
        if backend == "tavily":
            return await self._search_tavily(query, limit)
        if backend == "duckduckgo":
            return await self._search_duckduckgo(query, limit)
        return ToolResult.fail("No web search backend is available.")

    def _resolve_backend(self) -> str:
        configured = self.config.search_backend
        if configured == "tavily":
            if not self.config.tavily.api_key:
                return "unavailable"
            return "tavily"
        if configured == "duckduckgo":
            return "duckduckgo" if self.config.duckduckgo.enabled else "unavailable"
        if self.config.tavily.api_key:
            return "tavily"
        if self.config.duckduckgo.enabled:
            return "duckduckgo"
        return "unavailable"

    async def _search_tavily(self, query: str, max_results: int) -> ToolResult:
        if not self.config.tavily.api_key:
            return ToolResult.fail("Tavily search is unavailable: tools.web.tavily.api_key is not configured.")
        try:
            response = await asyncio.to_thread(
                self._run_tavily_search,
                query,
                max_results,
            )
        except Exception as exc:  # noqa: BLE001 - third-party SDK errors become tool errors
            return ToolResult.fail(f"web_search failed via Tavily: {_format_exception(exc)}")

        results = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or item.get("snippet") or ""),
                "score": item.get("score"),
            }
            for item in response.get("results", [])
            if isinstance(item, dict)
        ]
        return _search_result("tavily", query, results[:max_results])

    async def _search_duckduckgo(self, query: str, max_results: int) -> ToolResult:
        if not self.config.duckduckgo.enabled:
            return ToolResult.fail("DuckDuckGo fallback is disabled in tools.web.duckduckgo.enabled.")
        try:
            response = await asyncio.to_thread(
                self._run_duckduckgo_search,
                query,
                max_results,
            )
        except Exception as exc:  # noqa: BLE001 - third-party SDK errors become tool errors
            return ToolResult.fail(f"web_search failed via DuckDuckGo: {_format_exception(exc)}")

        results = [
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("href") or item.get("url") or ""),
                "content": str(item.get("body") or item.get("content") or ""),
            }
            for item in response
            if isinstance(item, dict)
        ]
        return _search_result("duckduckgo", query, results[:max_results])

    def _run_tavily_search(self, query: str, max_results: int) -> dict[str, Any]:
        client_factory = self.tavily_client_factory or _default_tavily_client_factory
        client = client_factory(self.config.tavily.api_key or "")
        response = client.search(query=query, max_results=max_results)
        return response if isinstance(response, dict) else {"results": []}

    def _run_duckduckgo_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        ddgs_factory = self.ddgs_factory or _default_ddgs_factory
        searcher = ddgs_factory(self.config.timeout_seconds)
        response = searcher.text(query, max_results=max_results, backend="duckduckgo")
        return list(response)


def _default_tavily_client_factory(api_key: str) -> Any:
    module = importlib.import_module("tavily")
    return module.TavilyClient(api_key=api_key)


def _default_ddgs_factory(timeout_seconds: float) -> Any:
    module = importlib.import_module("ddgs")
    return module.DDGS(timeout=timeout_seconds)


def _join_reader_url(base_url: str, url: str) -> str:
    return f"{base_url.rstrip('/')}/{url}"


def _validate_fetch_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "web_fetch only supports absolute http/https URLs."
    return None


def _parse_jina_response(response: httpx.Response, *, fallback_url: str) -> tuple[str, str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "", fallback_url, response.text
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        title = str(data.get("title") or "") if isinstance(data, dict) else ""
        source_url = str(data.get("url") or fallback_url) if isinstance(data, dict) else fallback_url
        content = str(data.get("content") or data.get("text") or "") if isinstance(data, dict) else ""
        if content:
            return title, source_url, content
    return "", fallback_url, response.text


@dataclass(frozen=True)
class DirectFetchResponse:
    response: httpx.Response
    content: bytes
    download_truncated: bool


class DirectFetchError(Exception):
    """受限 direct GET 失败。"""


async def _fetch_direct_get(
    url: str,
    *,
    timeout: float,
    max_chars: int,
    transport: httpx.AsyncBaseTransport | None,
) -> DirectFetchResponse:
    max_bytes = _direct_byte_limit(max_chars)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url, headers=_DIRECT_GET_HEADERS) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if _is_known_binary_content_type(content_type):
                raise DirectFetchError(f"Unsupported direct GET content type: {content_type}")
            content, download_truncated = await _read_limited_response(response, max_bytes=max_bytes)

    if _looks_binary(content):
        raise DirectFetchError("Unsupported direct GET response: content appears to be binary.")
    if not _is_supported_text_content_type(content_type) and not _looks_like_text(content):
        media_type = _media_type(content_type) or "unknown"
        raise DirectFetchError(f"Unsupported direct GET content type: {media_type}")
    return DirectFetchResponse(
        response=response,
        content=content,
        download_truncated=download_truncated,
    )


async def _read_limited_response(response: httpx.Response, *, max_bytes: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    received = 0
    download_truncated = False
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        remaining = max_bytes - received
        if remaining <= 0:
            download_truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            download_truncated = True
            break
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks), download_truncated


def _direct_byte_limit(max_chars: int) -> int:
    if max_chars <= 0:
        return _DIRECT_MIN_BYTES
    return min(max(max_chars * 4, _DIRECT_MIN_BYTES), _DIRECT_MAX_BYTES)


def _parse_direct_response(
    direct: DirectFetchResponse,
    *,
    fallback_url: str,
) -> tuple[str, str, str]:
    response = direct.response
    content_type = response.headers.get("content-type", "").lower()
    source_url = str(response.url) if response.url else fallback_url
    text = _decode_response_text(response, direct.content)
    if _is_json_content_type(content_type):
        return "", source_url, _format_json_text(text)
    if _is_html_content_type(content_type) or "<html" in text[:500].lower():
        title, text = _extract_html_text(text)
        return title, source_url, text
    return "", source_url, _clean_text(text)


def _decode_response_text(response: httpx.Response, content: bytes) -> str:
    candidates = _encoding_candidates(response, content)
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def _encoding_candidates(response: httpx.Response, content: bytes) -> list[str]:
    candidates: list[str] = []
    header_encoding = _charset_from_content_type(response.headers.get("content-type", ""))
    if header_encoding:
        candidates.append(header_encoding)
    meta_encoding = _charset_from_html(content)
    if meta_encoding:
        candidates.append(meta_encoding)
    candidates.extend(["utf-8", "gb18030"])
    deduped: list[str] = []
    for encoding in candidates:
        normalized = encoding.strip().lower()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _charset_from_content_type(content_type: str) -> str | None:
    match = re.search(r"charset\s*=\s*['\"]?([^;,'\"\s]+)", content_type, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _charset_from_html(content: bytes) -> str | None:
    head = content[:4096].decode("ascii", errors="ignore")
    match = re.search(r"<meta[^>]+charset=['\"]?\s*([A-Za-z0-9._-]+)", head, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(
        r"<meta[^>]+content=['\"][^'\"]*charset=([A-Za-z0-9._-]+)",
        head,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _format_json_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except ValueError:
        return _clean_text(text)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _is_known_binary_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    if not media_type:
        return False
    return (
        media_type.startswith(("image/", "audio/", "video/", "font/"))
        or media_type
        in {
            "application/octet-stream",
            "application/pdf",
            "application/zip",
            "application/gzip",
            "application/x-gzip",
            "application/x-tar",
        }
    )


def _is_supported_text_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    if not media_type:
        return True
    return (
        media_type.startswith("text/")
        or media_type
        in {
            "application/json",
            "application/javascript",
            "application/x-javascript",
            "application/xml",
            "application/xhtml+xml",
            "application/rss+xml",
            "application/atom+xml",
            "application/x-www-form-urlencoded",
        }
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    )


def _is_json_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    return media_type == "application/json" or media_type.endswith("+json")


def _is_html_content_type(content_type: str) -> bool:
    media_type = _media_type(content_type)
    return media_type in {"text/html", "application/xhtml+xml"} or media_type.endswith("+html")


def _looks_binary(content: bytes) -> bool:
    sample = content[:1024]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    control_bytes = sum(byte < 32 and byte not in {9, 10, 12, 13} for byte in sample)
    return control_bytes / len(sample) > 0.30


def _looks_like_text(content: bytes) -> bool:
    if not content:
        return True
    return any(
        _can_decode(content, encoding)
        for encoding in _encoding_candidates(httpx.Response(200), content)
    )


def _can_decode(content: bytes, encoding: str) -> bool:
    try:
        content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return False
    return True


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"}:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.body_parts.append(text)


def _extract_html_text(value: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(value)
    title = " ".join(parser.title_parts).strip()
    content = _clean_text("\n".join(part.strip() for part in parser.body_parts if part.strip()))
    return title, content


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.split("\n")]
    compact_lines: list[str] = []
    blank_seen = False
    for line in lines:
        if line:
            compact_lines.append(line)
            blank_seen = False
        elif not blank_seen and compact_lines:
            compact_lines.append("")
            blank_seen = True
    return "\n".join(compact_lines).strip()


def _truncate(content: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0 or len(content) <= max_chars:
        return content, False
    return content[:max_chars], True


def _format_fetch_result(
    *,
    source_url: str,
    title: str,
    content: str,
    truncated: bool,
    backend: str,
    fallback_from: str | None = None,
) -> str:
    lines = [
        f"backend: {backend}",
        f"url: {source_url}",
    ]
    if fallback_from:
        lines.append(f"fallback_from: {fallback_from}")
    if title:
        lines.append(f"title: {title}")
    if truncated:
        lines.append("truncated: true")
    lines.extend(["", content])
    return "\n".join(lines)


def _search_result(backend: str, query: str, results: list[dict[str, Any]]) -> ToolResult:
    if not results:
        return ToolResult.ok(
            f"backend: {backend}\nquery: {query}\nresults: No results.",
            data={"backend": backend, "query": query, "results": []},
        )
    lines = [f"backend: {backend}", f"query: {query}", "results:"]
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item.get('title') or '(untitled)'}")
        if item.get("url"):
            lines.append(f"   URL: {item['url']}")
        if item.get("content"):
            lines.append(f"   Snippet: {item['content']}")
    return ToolResult.ok(
        "\n".join(lines),
        data={"backend": backend, "query": query, "results": results},
    )


def _format_exception(exc: Exception | None) -> str:
    if exc is None:
        return "none"
    message = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"{exc.response.status_code} {exc.response.reason_phrase}"
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


async def _check_web_permission(
    approver: Approver,
    permission: WebPermissionConfig,
    target: str,
    *,
    summary: str,
) -> ToolResult | None:
    if permission.default == "allow":
        return None
    if permission.default == "deny":
        return ToolResult.fail("Web request denied by permissions.web.default=deny.")
    allowed = await approver.require("web", target, summary=summary)
    if allowed:
        return None
    return ToolResult.fail(f"Denied web request for {target}")
