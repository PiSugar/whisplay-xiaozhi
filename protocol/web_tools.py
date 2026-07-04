"""
Web access MCP tools.

Provides helpers for fetching readable page text, following links on a page,
and running general web/news/site searches from XiaoZhi tool calls.
"""

import asyncio
import html
import logging
import os
import re
from typing import Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from xml.etree import ElementTree

import requests

import config

log = logging.getLogger("mcp.web")

ProgressCallback = Callable[[str | None], None]


FETCH_WEBPAGE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "Web page URL to fetch. Optional when opening a link from the previous page.",
        },
        "current_url": {
            "type": "string",
            "description": "Base page URL containing the link to open.",
        },
        "link_text": {
            "type": "string",
            "description": "Visible link text to open, for example '科技' or 'Technology'.",
        },
        "link_index": {
            "type": "number",
            "description": "1-based link index from a previous fetch_webpage result.",
        },
        "max_chars": {
            "type": "number",
            "description": "Optional maximum characters to return.",
        },
    },
}

FETCH_WEBPAGE_DESCRIPTION = (
    "Fetch a web page and return readable text plus links. Can also open a link "
    "from the current or previous page by link text or link index."
)

WEB_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query.",
        },
        "num_results": {
            "type": "number",
            "description": "Optional number of search results to return.",
        },
        "search_type": {
            "type": "string",
            "description": "Optional search type: web, news, or sites.",
        },
    },
    "required": ["query"],
}

WEB_SEARCH_DESCRIPTION = (
    "Search the web and return a compact list of result titles and URLs. "
    "Use search_type=news for news and search_type=sites for configured site search."
)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_LAST_PAGE: dict = {"url": "", "links": []}


class _ReadableTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[dict] = []
        self._in_title = False
        self._skip_depth = 0
        self._parts: list[str] = []
        self._current_link: dict | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "a" and self._skip_depth == 0:
            href = dict(attrs).get("href", "").strip()
            if href:
                self._current_link = {"href": href}
                self._current_link_text = []
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "a" and self._current_link:
            text = _clean_text(" ".join(self._current_link_text))
            href = self._current_link.get("href", "")
            if text and href:
                self.links.append({"text": text, "url": href})
            self._current_link = None
            self._current_link_text = []
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        self._parts.append(data)
        if self._current_link is not None:
            self._current_link_text.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self._parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class _GoogleLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._current_url = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href", "")
        url = _extract_google_result_url(href)
        if not url:
            return
        self._current_url = url
        self._current_text = []

    def handle_endtag(self, tag: str):
        if tag.lower() != "a" or not self._current_url:
            return
        title = _clean_text(" ".join(self._current_text))
        if title and not _is_google_internal_url(self._current_url):
            self.results.append({"title": title, "url": self._current_url})
        self._current_url = ""
        self._current_text = []

    def handle_data(self, data: str):
        if self._current_url:
            self._current_text.append(data)


class _DuckDuckGoLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._current_url = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        classes = attr_map.get("class", "")
        if "result__a" not in classes.split():
            return
        url = _extract_duckduckgo_result_url(attr_map.get("href", ""))
        if not url:
            return
        self._current_url = url
        self._current_text = []

    def handle_endtag(self, tag: str):
        if tag.lower() != "a" or not self._current_url:
            return
        title = _clean_text(" ".join(self._current_text))
        if title:
            self.results.append({"title": title, "url": self._current_url})
        self._current_url = ""
        self._current_text = []

    def handle_data(self, data: str):
        if self._current_url:
            self._current_text.append(data)


def is_enabled() -> bool:
    return config.WEB_TOOLS_ENABLED


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n...[truncated {len(text) - limit} chars]"


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _proxy_value() -> str:
    return (
        config.WEB_TOOL_PROXY
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("ALL_PROXY", "").strip()
    )


def _proxies() -> dict[str, str] | None:
    proxy = _proxy_value()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }


def _fetch(url: str) -> requests.Response:
    return requests.get(
        url,
        headers=_headers(),
        proxies=_proxies(),
        timeout=config.WEB_TOOL_TIMEOUT_SEC,
    )


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("url is required")
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be an http(s) URL")
    return url


def _normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _dedupe_links(links: list[dict], base_url: str) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for link in links:
        text = _clean_text(str(link.get("text", "")))
        href = str(link.get("url", "")).strip()
        if not text or not href:
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        key = (text, url)
        if key in seen:
            continue
        seen.add(key)
        result.append({"index": len(result) + 1, "text": text[:80], "url": url})
        if len(result) >= config.WEB_TOOL_LINK_LIMIT:
            break
    return result


def _pick_link(links: list[dict], link_text: str = "", link_index=None) -> dict:
    if link_index not in (None, ""):
        try:
            index = int(link_index)
        except (TypeError, ValueError) as e:
            raise ValueError("link_index must be a number") from e
        for link in links:
            if int(link.get("index", 0)) == index:
                return link
        raise ValueError(f"link_index {index} was not found")

    needle = _normalize_match_text(link_text)
    if not needle:
        raise ValueError("link_text or link_index is required")

    matches = []
    for link in links:
        haystack = _normalize_match_text(str(link.get("text", "")))
        if haystack == needle:
            return link
        if needle in haystack or haystack in needle:
            matches.append(link)
    if matches:
        return matches[0]
    raise ValueError(f"link_text '{link_text}' was not found")


def _extract_page_sync(url: str, max_chars: int) -> dict:
    response = _fetch(url)
    response.raise_for_status()
    parser = _ReadableTextParser()
    parser.feed(response.text)
    links = _dedupe_links(parser.links, response.url)
    _LAST_PAGE["url"] = response.url
    _LAST_PAGE["links"] = links
    return {
        "url": response.url,
        "status_code": response.status_code,
        "title": _clean_text(parser.title),
        "text": _clip(parser.text(), max_chars),
        "links": links,
    }


def _short_label(value: str, limit: int = 96) -> str:
    value = _clean_text(value)
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


async def fetch_webpage(params: dict, progress_callback: ProgressCallback | None = None) -> dict:
    link_text = str(params.get("link_text", "")).strip()
    link_index = params.get("link_index")
    requested_url = str(params.get("url", "")).strip()
    current_url = str(params.get("current_url", "")).strip()

    max_chars = params.get("max_chars", config.WEB_TOOL_TEXT_LIMIT)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError) as e:
        raise ValueError("max_chars must be a number") from e
    max_chars = max(256, min(max_chars, config.WEB_TOOL_TEXT_LIMIT))

    if link_text or link_index not in (None, ""):
        if requested_url:
            if progress_callback:
                progress_callback(f"fetch base\n{_short_label(_normalize_url(requested_url), 120)}")
            base_page = await asyncio.to_thread(
                _extract_page_sync,
                _normalize_url(requested_url),
                config.WEB_TOOL_TEXT_LIMIT,
            )
            links = base_page["links"]
            base_url = base_page["url"]
        else:
            links = _LAST_PAGE.get("links", [])
            base_url = current_url or _LAST_PAGE.get("url", "")
        if not links:
            raise ValueError("no previous page links are available; pass url first")
        picked = _pick_link(links, link_text=link_text, link_index=link_index)
        url = urljoin(base_url, picked["url"])
    else:
        url = _normalize_url(requested_url or current_url)

    log.info("fetching webpage: %s", url)
    if progress_callback:
        progress_callback(f"fetch_webpage\n{_short_label(url, 120)}")
    result = await asyncio.to_thread(_extract_page_sync, url, max_chars)
    if progress_callback:
        title = result.get("title") or result.get("url") or "done"
        text_len = len(str(result.get("text", "")))
        progress_callback(f"fetched {result.get('status_code', '')}\n{_short_label(str(title), 120)}\n{text_len} chars")
    return result


async def get_webpage_text(params: dict) -> dict:
    return await fetch_webpage(params)


def _extract_google_result_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/url?"):
        query = parse_qs(urlparse(href).query)
        return unquote(query.get("q", [""])[0])
    if href.startswith("/search?") or href.startswith("/preferences?"):
        return ""
    if href.startswith("/"):
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def _extract_duckduckgo_result_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com"):
        query = parse_qs(parsed.query)
        return unquote(query.get("uddg", [""])[0])
    if parsed.scheme in ("http", "https"):
        return href
    return ""


def _is_google_internal_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return (
        host.endswith("google.com")
        or host.endswith("google.com.hk")
        or host.endswith("googleusercontent.com")
    )


def _extract_rss_items(xml_text: str, num_results: int) -> list[dict]:
    root = ElementTree.fromstring(xml_text)
    results: list[dict] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title") or "")
        url = _clean_text(item.findtext("link") or "")
        snippet = _clean_text(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        pub_date = _clean_text(item.findtext("pubDate") or "")
        source_node = item.find("source")
        source = _clean_text(source_node.text or "") if source_node is not None else ""
        if title and url:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": source,
                    "published": pub_date,
                }
            )
        if len(results) >= num_results:
            break
    return results


def _search_google_news_rss_sync(query: str, num_results: int) -> dict:
    search_url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    response = _fetch(search_url)
    response.raise_for_status()
    results = _extract_rss_items(response.text, num_results)
    log.info("google news rss returned %d results", len(results))
    return {
        "query": query,
        "url": response.url,
        "source": "google_news_rss",
        "results": results,
    }


def _search_duckduckgo_sync(query: str, num_results: int) -> dict:
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = _fetch(search_url)
    response.raise_for_status()
    parser = _DuckDuckGoLinkParser()
    parser.feed(response.text)

    seen: set[str] = set()
    results: list[dict] = []
    for item in parser.results:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        results.append(item)
        if len(results) >= num_results:
            break

    log.info("duckduckgo fallback returned %d results", len(results))
    return {
        "query": query,
        "url": response.url,
        "source": "duckduckgo_html_fallback",
        "results": results,
    }


def _search_google_api_sync(query: str, num_results: int) -> dict:
    if not config.GOOGLE_SEARCH_API_KEY or not config.GOOGLE_SEARCH_ENGINE_ID:
        return {"query": query, "url": "", "source": "google_api_unconfigured", "results": []}

    response = requests.get(
        "https://customsearch.googleapis.com/customsearch/v1",
        params={
            "key": config.GOOGLE_SEARCH_API_KEY,
            "cx": config.GOOGLE_SEARCH_ENGINE_ID,
            "q": query,
            "num": max(1, min(num_results, 10)),
            "hl": "zh-CN",
        },
        proxies=_proxies(),
        timeout=config.WEB_TOOL_TIMEOUT_SEC,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get("items", []):
        title = _clean_text(item.get("title", ""))
        url = _clean_text(item.get("link", ""))
        snippet = _clean_text(item.get("snippet", ""))
        if title and url:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "source": item.get("displayLink", ""),
                }
            )
        if len(results) >= num_results:
            break

    log.info("google programmable search api returned %d results", len(results))
    return {
        "query": query,
        "url": data.get("url", {}).get("template", response.url),
        "source": "google_programmable_search_api",
        "results": results,
    }


def _search_google_sync(query: str, num_results: int) -> dict:
    if config.GOOGLE_SEARCH_API_KEY and config.GOOGLE_SEARCH_ENGINE_ID:
        try:
            api_result = _search_google_api_sync(query, num_results)
            if api_result["results"]:
                return api_result
            log.warning("Google Programmable Search API returned no results; falling back")
        except requests.RequestException as e:
            log.warning("Google Programmable Search API failed; falling back: %s", e)

    search_url = (
        "https://www.google.com/search"
        f"?q={quote_plus(query)}&num={num_results}&hl=zh-CN&gbv=1"
    )
    try:
        response = _fetch(search_url)
        response.raise_for_status()
    except requests.RequestException as e:
        log.warning("google web search failed; falling back to Google News RSS: %s", e)
        news_result = _search_google_news_rss_sync(query, num_results)
        if news_result["results"]:
            return news_result
        log.warning("Google News RSS returned no results; falling back to DuckDuckGo HTML")
        return _search_duckduckgo_sync(query, num_results)

    parser = _GoogleLinkParser()
    parser.feed(response.text)

    seen: set[str] = set()
    results: list[dict] = []
    for item in parser.results:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        item["url"] = urljoin(response.url, url)
        results.append(item)
        if len(results) >= num_results:
            break

    if results:
        log.info("google web search returned %d results", len(results))
        return {
            "query": query,
            "url": response.url,
            "source": "google_web",
            "results": results,
        }

    log.warning("google web search returned no parseable results; falling back to Google News RSS")
    news_result = _search_google_news_rss_sync(query, num_results)
    if news_result["results"]:
        return news_result
    log.warning("Google News RSS returned no results; falling back to DuckDuckGo HTML")
    return _search_duckduckgo_sync(query, num_results)


def _search_web_sync(query: str, num_results: int, search_type: str) -> dict:
    search_type = (search_type or "web").strip().lower()
    if search_type in ("news", "google_news"):
        try:
            news_result = _search_google_news_rss_sync(query, num_results)
            if news_result["results"]:
                return news_result
            log.warning("Google News RSS returned no results; falling back to DuckDuckGo HTML")
        except requests.RequestException as e:
            log.warning("Google News RSS failed; falling back to DuckDuckGo HTML: %s", e)
        return _search_duckduckgo_sync(query, num_results)

    if search_type in ("sites", "site", "configured_sites"):
        if config.GOOGLE_SEARCH_API_KEY and config.GOOGLE_SEARCH_ENGINE_ID:
            try:
                api_result = _search_google_api_sync(query, num_results)
                if api_result["results"]:
                    return api_result
                log.warning("site search API returned no results; falling back to DuckDuckGo HTML")
            except requests.RequestException as e:
                log.warning("site search API failed; falling back to DuckDuckGo HTML: %s", e)
        else:
            log.info("site search API is not configured; using DuckDuckGo HTML")
        return _search_duckduckgo_sync(query, num_results)

    if search_type not in ("web", "general", ""):
        log.info("unknown search_type=%s; using web search", search_type)
    return _search_duckduckgo_sync(query, num_results)


async def web_search(params: dict, progress_callback: ProgressCallback | None = None) -> dict:
    query = str(params.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    num_results = params.get("num_results", config.WEB_SEARCH_RESULT_LIMIT)
    try:
        num_results = int(num_results)
    except (TypeError, ValueError) as e:
        raise ValueError("num_results must be a number") from e
    num_results = max(1, min(num_results, config.WEB_SEARCH_RESULT_LIMIT))
    search_type = str(params.get("search_type", "web")).strip() or "web"
    log.info("running web search type=%s query=%s", search_type, query)
    if progress_callback:
        progress_callback(f"web_search {search_type}\n{_short_label(query, 120)}")
    result = await asyncio.to_thread(_search_web_sync, query, num_results, search_type)
    if progress_callback:
        results = result.get("results", [])
        source = result.get("source", "search")
        first = results[0].get("title", "") if results else "no results"
        progress_callback(f"{source}\n{len(results)} results\n{_short_label(str(first), 120)}")
    return result


async def google_search(params: dict) -> dict:
    return await web_search(params)
