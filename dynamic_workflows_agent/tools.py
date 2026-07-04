from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
_DUCKDUCKGO_HTML_ENDPOINTS = (
    "https://html.duckduckgo.com/html/",
    "https://duckduckgo.com/html/",
)
_BING_RSS_ENDPOINT = "https://www.bing.com/search"


class ToolError(RuntimeError):
    """Raised when a tool request is invalid or cannot be executed."""


@dataclass(slots=True)
class ToolResult:
    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolRegistry:
    def __init__(self, *, workspace_root: str | Path = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._tools: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_files": self._search_files,
            "web_search": self._web_search,
            "fetch_url": self._fetch_url,
        }

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files under a workspace-relative directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "root": {
                                "type": "string",
                                "description": "Workspace-relative directory to list. Use '.' for the workspace root.",
                            },
                            "max_results": {"type": "integer", "description": "Maximum number of files to return."},
                        },
                        "required": ["root"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a UTF-8 text file from the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Workspace-relative file path."},
                            "max_chars": {"type": "integer", "description": "Maximum characters to return."},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_files",
                    "description": "Search workspace files for a regex or literal pattern.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regex pattern to search for."},
                            "root": {"type": "string", "description": "Workspace-relative directory to search."},
                            "max_results": {"type": "integer", "description": "Maximum matches to return."},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the public web for current information and return titles, URLs, and snippets.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query."},
                            "max_results": {"type": "integer", "description": "Maximum results to return."},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "Fetch a public URL as text and return a truncated text extract.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "HTTP or HTTPS URL."},
                            "max_chars": {"type": "integer", "description": "Maximum characters to return."},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    def run(self, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        args = arguments or {}
        if name not in self._tools:
            return ToolResult(name=name, arguments=args, ok=False, summary="unknown tool", error=f"unknown tool: {name}")
        try:
            return self._tools[name](args)
        except Exception as exc:
            return ToolResult(name=name, arguments=args, ok=False, summary=f"{name} failed", error=str(exc))

    def _list_files(self, args: dict[str, Any]) -> ToolResult:
        root = self._resolve_workspace_path(str(args.get("root") or "."))
        max_results = _int_arg(args.get("max_results"), default=200, low=1, high=1000)
        if not root.exists():
            raise ToolError(f"path does not exist: {root.relative_to(self.workspace_root)}")
        if not root.is_dir():
            raise ToolError(f"path is not a directory: {root.relative_to(self.workspace_root)}")
        files: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if _skip_path(path):
                continue
            if path.is_file():
                rel = str(path.relative_to(self.workspace_root))
                files.append({"path": rel, "size": path.stat().st_size})
            if len(files) >= max_results:
                break
        return ToolResult(
            name="list_files",
            arguments=args,
            ok=True,
            summary=f"listed {len(files)} file(s)",
            data={"files": files, "truncated": len(files) >= max_results},
        )

    def _read_file(self, args: dict[str, Any]) -> ToolResult:
        path = self._resolve_workspace_path(str(args.get("path") or ""))
        max_chars = _int_arg(args.get("max_chars"), default=12000, low=1, high=50000)
        if not path.exists() or not path.is_file():
            raise ToolError(f"file not found: {args.get('path')}")
        text = path.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        rel = str(path.relative_to(self.workspace_root))
        return ToolResult(
            name="read_file",
            arguments=args,
            ok=True,
            summary=f"read {rel} ({min(len(text), max_chars)} chars)",
            data={"path": rel, "content": text[:max_chars], "truncated": truncated},
        )

    def _search_files(self, args: dict[str, Any]) -> ToolResult:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            raise ToolError("pattern is required")
        root = self._resolve_workspace_path(str(args.get("root") or "."))
        max_results = _int_arg(args.get("max_results"), default=100, low=1, high=500)
        matches = self._rg_search(pattern=pattern, root=root, max_results=max_results)
        return ToolResult(
            name="search_files",
            arguments=args,
            ok=True,
            summary=f"found {len(matches)} match(es)",
            data={"matches": matches, "truncated": len(matches) >= max_results},
        )

    def _web_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        max_results = _int_arg(args.get("max_results"), default=5, low=1, high=10)
        headers = {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        errors: list[str] = []
        for endpoint in _DUCKDUCKGO_HTML_ENDPOINTS:
            url = endpoint + "?" + urllib.parse.urlencode({"q": query})
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=12) as response:
                    page = response.read().decode("utf-8", errors="replace")
            except Exception as exc:
                errors.append(f"{urllib.parse.urlparse(endpoint).netloc}: {exc}")
                continue
            results = _parse_duckduckgo_results(page, max_results=max_results)
            if results:
                return ToolResult(
                    name="web_search",
                    arguments=args,
                    ok=True,
                    summary=f"web search returned {len(results)} result(s)",
                    data={"query": query, "results": results, "source": endpoint},
                )
            if _duckduckgo_no_results(page):
                return ToolResult(
                    name="web_search",
                    arguments=args,
                    ok=True,
                    summary="web search returned 0 result(s)",
                    data={"query": query, "results": [], "source": endpoint, "empty_reason": "no results found"},
                )
            errors.append(f"{urllib.parse.urlparse(endpoint).netloc}: {_duckduckgo_empty_reason(page)}")

        bing_url = _BING_RSS_ENDPOINT + "?" + urllib.parse.urlencode({"format": "rss", "q": query})
        request = urllib.request.Request(bing_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                feed = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"www.bing.com/rss: {exc}")
        else:
            results = _parse_bing_rss_results(feed, max_results=max_results)
            if results:
                return ToolResult(
                    name="web_search",
                    arguments=args,
                    ok=True,
                    summary=f"web search returned {len(results)} result(s)",
                    data={
                        "query": query,
                        "results": results,
                        "source": _BING_RSS_ENDPOINT,
                        "fallback_reason": "; ".join(errors[-2:]),
                    },
                )
            if _bing_rss_no_results(feed):
                return ToolResult(
                    name="web_search",
                    arguments=args,
                    ok=True,
                    summary="web search returned 0 result(s)",
                    data={"query": query, "results": [], "source": _BING_RSS_ENDPOINT, "empty_reason": "no results found"},
                )
            errors.append(f"www.bing.com/rss: {_bing_empty_reason(feed)}")
        raise ToolError("; ".join(errors) or "web search returned no parseable results")

    def _fetch_url(self, args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("url must start with http:// or https://")
        max_chars = _int_arg(args.get("max_chars"), default=12000, low=1, high=50000)
        request = urllib.request.Request(url, headers={"User-Agent": "dynamic-workflows-agent/0.1"})
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_chars * 4)
        text = raw.decode("utf-8", errors="replace")
        text = _html_to_text(text) if "html" in content_type.lower() or "<html" in text[:500].lower() else text
        truncated = len(text) > max_chars
        return ToolResult(
            name="fetch_url",
            arguments=args,
            ok=True,
            summary=f"fetched {url} ({min(len(text), max_chars)} chars)",
            data={"url": url, "content": text[:max_chars], "truncated": truncated},
        )

    def _resolve_workspace_path(self, value: str) -> Path:
        if not value:
            raise ToolError("path is required")
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.workspace_root / candidate).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ToolError(f"path escapes workspace: {value}") from exc
        return resolved

    def _rg_search(self, *, pattern: str, root: Path, max_results: int) -> list[dict[str, Any]]:
        if not root.exists():
            raise ToolError(f"root does not exist: {root}")
        try:
            completed = subprocess.run(
                ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", str(max_results), pattern, str(root)],
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return self._python_search(pattern=pattern, root=root, max_results=max_results)
        if completed.returncode not in {0, 1}:
            raise ToolError(completed.stderr.strip() or "rg failed")
        matches = []
        for line in completed.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            path, line_no, text = parts
            resolved = Path(path).resolve()
            try:
                rel = str(resolved.relative_to(self.workspace_root))
            except ValueError:
                rel = path
            matches.append({"path": rel, "line": int(line_no), "text": text[:500]})
            if len(matches) >= max_results:
                break
        return matches

    def _python_search(self, *, pattern: str, root: Path, max_results: int) -> list[dict[str, Any]]:
        regex = re.compile(pattern)
        matches: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if _skip_path(path) or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(
                        {"path": str(path.relative_to(self.workspace_root)), "line": line_number, "text": line[:500]}
                    )
                    if len(matches) >= max_results:
                        return matches
        return matches


def _int_arg(value: Any, *, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _skip_path(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "runs"})


def _parse_duckduckgo_results(page: str, *, max_results: int) -> list[dict[str, str]]:
    parser = _DuckDuckGoHTMLParser(max_results=max_results)
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        pass
    if parser.results:
        return parser.results[:max_results]
    return _parse_duckduckgo_results_regex(page, max_results=max_results)


class _DuckDuckGoHTMLParser(HTMLParser):
    _TITLE_CLASSES = {"result__a", "result-link", "result-title"}
    _SNIPPET_CLASSES = {"result__snippet", "result-snippet"}

    def __init__(self, *, max_results: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_results = max_results
        self.results: list[dict[str, str]] = []
        self._seen_urls: set[str] = set()
        self._current: dict[str, str] | None = None
        self._capture_field = ""
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.results) >= self.max_results:
            return
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and classes & self._TITLE_CLASSES:
            self._flush_current()
            if len(self.results) >= self.max_results:
                return
            self._current = {
                "title": "",
                "url": _normalize_duckduckgo_url(attrs_dict.get("href", "")),
                "snippet": "",
            }
            self._capture_field = "title"
            self._capture_depth = 1
            return
        if self._current is not None and classes & self._SNIPPET_CLASSES:
            self._capture_field = "snippet"
            self._capture_depth = 1
            return
        if self._capture_field:
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture_field:
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                self._capture_field = ""
                self._capture_depth = 0

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture_field:
            self._current[self._capture_field] += data

    def close(self) -> None:
        super().close()
        self._flush_current()

    def _flush_current(self) -> None:
        if self._current is None:
            return
        title = _clean_html_text(self._current.get("title", ""))
        url = self._current.get("url", "").strip()
        snippet = _clean_html_text(self._current.get("snippet", ""))
        self._current = None
        if not title or not url or url in self._seen_urls:
            return
        if len(self.results) >= self.max_results:
            return
        self._seen_urls.add(url)
        self.results.append({"title": title, "url": url, "snippet": snippet})


def _parse_duckduckgo_results_regex(page: str, *, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    anchor_pattern = re.compile(
        r'<a\b(?=[^>]*\bclass=(["\'])(?=[^"\']*(?:result__a|result-link|result-title))[^"\']*\1)'
        r'(?P<attrs>[^>]*)>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in anchor_pattern.finditer(page):
        href_match = re.search(r'\bhref=(["\'])(?P<href>.*?)\1', match.group("attrs"), re.IGNORECASE | re.DOTALL)
        if not href_match:
            continue
        url = _normalize_duckduckgo_url(href_match.group("href"))
        if not url or url in seen_urls:
            continue
        title = _clean_html_text(match.group("title"))
        if not title:
            continue
        snippet = ""
        tail = page[match.end() : match.end() + 4000]
        snippet_match = re.search(
            r'<(?P<tag>[a-z0-9]+)\b(?=[^>]*\bclass=(["\'])(?=[^"\']*(?:result__snippet|result-snippet))'
            r'[^"\']*\2)[^>]*>(?P<snippet>.*?)</(?P=tag)>',
            tail,
            re.IGNORECASE | re.DOTALL,
        )
        if snippet_match:
            snippet = _clean_html_text(snippet_match.group("snippet"))
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _parse_bing_rss_results(feed: str, *, max_results: int) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(feed)
    except ET.ParseError:
        return []
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in root.findall(".//item"):
        title = _xml_child_text(item, "title")
        url = _xml_child_text(item, "link")
        snippet = _xml_child_text(item, "description")
        published = _xml_child_text(item, "pubDate")
        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        result = {"title": title, "url": url, "snippet": snippet}
        if published:
            result["published"] = published
        results.append(result)
        if len(results) >= max_results:
            break
    return results


def _xml_child_text(item: ET.Element, child_name: str) -> str:
    child = item.find(child_name)
    if child is None or child.text is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(child.text)).strip()


def _normalize_duckduckgo_url(raw_url: str) -> str:
    url = html.unescape(_strip_tags(raw_url)).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urllib.parse.urljoin("https://duckduckgo.com", url)
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("uddg"):
        return query["uddg"][0]
    return url


def _duckduckgo_no_results(page: str) -> bool:
    text = _html_to_text(page).lower()
    return "no results found" in text or "no more results" in text


def _duckduckgo_empty_reason(page: str) -> str:
    lower = page.lower()
    if any(token in lower for token in ("captcha", "anomaly", "automated requests", "bot detection")):
        return "DuckDuckGo returned an anti-bot or challenge page"
    if "result__a" in lower or "result-link" in lower or "result-title" in lower:
        return "DuckDuckGo returned result markup but no parser could extract results"
    return "DuckDuckGo returned an unexpected page without search results"


def _bing_rss_no_results(feed: str) -> bool:
    try:
        root = ET.fromstring(feed)
    except ET.ParseError:
        return False
    return root.tag.lower() == "rss" and root.find(".//item") is None


def _bing_empty_reason(feed: str) -> str:
    lower = feed.lower()
    if any(token in lower for token in ("captcha", "challenge", "turnstile", "bot detection")):
        return "Bing RSS returned an anti-bot or challenge page"
    try:
        root = ET.fromstring(feed)
    except ET.ParseError:
        return "Bing RSS returned non-XML content"
    if root.tag.lower() == "rss":
        return "Bing RSS returned a feed without parseable items"
    return "Bing RSS returned unexpected XML"


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?</style>", " ", value)
    value = _strip_tags(value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_html_text(value: str) -> str:
    value = _strip_tags(value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_tags(value: str) -> str:
    return re.sub(r"(?s)<.*?>", "", value)


def tool_results_to_prompt(results: list[ToolResult]) -> str:
    return json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2)
