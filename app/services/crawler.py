"""
Website crawler: fetch a site's pages and extract their readable text.

The crawler knows nothing about storage or vectors; it returns pages and the
route decides what to index. Fetching is injectable so tests run against an
in-memory site.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "chat-api-crawler/1.0 (+https://github.com/UAACC/CHAT-API)"
MAX_PAGES_HARD_CAP = 200
MIN_WORDS = 40
TRACKING_PARAMS = ("fbclid", "gclid", "msclkid", "ref", "mc_cid", "mc_eid")

REMOVE_TAGS = ("script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form", "svg", "iframe")
BLOCK_TAGS = ("p", "div", "section", "article", "main", "li", "ul", "ol", "table", "tr", "blockquote", "pre", "dl", "dt", "dd", "figure", "figcaption", "br", "hr")
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class FetchResult:
    status: int
    content_type: str = ""
    text: str = ""
    final_url: Optional[str] = None


Fetcher = Callable[[str], Awaitable[FetchResult]]


@dataclass
class Page:
    url: str
    title: str
    text: str
    depth: int

    @property
    def document_id(self) -> str:
        return document_id_for(self.url)


@dataclass
class CrawlResult:
    start_url: str
    pages: list[Page] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def document_id_for(url: str) -> str:
    """Stable id for a page, so re-crawls replace rather than duplicate."""
    return "url-" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:24]


def normalize_url(url: str) -> str:
    """Lower-case scheme/host, drop fragment and tracking parameters."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def _host_key(netloc: str) -> str:
    host = netloc.lower().split("@")[-1]
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, start_url: str) -> bool:
    return _host_key(urlsplit(url).netloc) == _host_key(urlsplit(start_url).netloc)


def filename_for(url: str) -> str:
    path = urlsplit(url).path.strip("/") or "index"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", path)[:120] + ".txt"


def extract_text(html: str, url: str) -> tuple[str, str]:
    """Readable (title, text) from a page's HTML."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or url

    for tag in soup(REMOVE_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": "navigation"}):
        tag.decompose()
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()

    container = soup.find("main") or soup.find("article") or soup.body or soup

    # Turn headings and block elements into line breaks before flattening.
    for tag in container.find_all(HEADING_TAGS):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")
    for tag in container.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    raw = container.get_text(" ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append(urljoin(base_url, href))
    return links


def _sitemap_urls(xml: str) -> list[str]:
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


async def http_fetch(url: str, client: Optional[httpx.AsyncClient] = None) -> FetchResult:
    """Default fetcher: GET with a short timeout, following redirects."""
    own = client is None
    client = client or httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(10.0, connect=3.0),
        follow_redirects=True,
    )
    try:
        r = await client.get(url)
        return FetchResult(
            status=r.status_code,
            content_type=r.headers.get("content-type", ""),
            text=r.text if "text" in r.headers.get("content-type", "") or "xml" in r.headers.get("content-type", "") else "",
            final_url=str(r.url),
        )
    except httpx.HTTPError as e:
        logger.info(f"Fetch failed for {url}: {e}")
        return FetchResult(status=0)
    finally:
        if own:
            await client.aclose()


async def crawl_site(
    start_url: str,
    max_pages: int = 30,
    max_depth: int = 3,
    fetch: Optional[Fetcher] = None,
    concurrency: int = 4,
) -> CrawlResult:
    """
    Breadth-first crawl of one site.

    Args:
        start_url: Where to begin; only this host is crawled
        max_pages: Stop after this many indexed pages (capped at 200)
        max_depth: Link distance from the start page
        fetch: Async fetcher, defaults to HTTP
        concurrency: Parallel fetches
    """
    max_pages = max(1, min(max_pages, MAX_PAGES_HARD_CAP))
    start = normalize_url(start_url)
    result = CrawlResult(start_url=start)

    client = None
    if fetch is None:
        client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=True,
        )

        async def fetch(url: str) -> FetchResult:  # noqa: F811
            return await http_fetch(url, client)

    try:
        robots = RobotFileParser()
        root = urlunsplit((urlsplit(start).scheme, urlsplit(start).netloc, "", "", ""))
        robots_res = await fetch(root + "/robots.txt")
        if robots_res.status == 200 and robots_res.text:
            robots.parse(robots_res.text.splitlines())
        else:
            robots.parse([])

        # `seen` holds every URL that has been queued or visited, so each page
        # is fetched at most once even when many pages link to it.
        seen: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]

        def enqueue(url: str, depth: int) -> None:
            n = normalize_url(url)
            if n not in seen and same_site(n, start) and depth <= max_depth:
                seen.add(n)
                queue.append((n, depth))

        sitemap_res = await fetch(root + "/sitemap.xml")
        if sitemap_res.status == 200 and sitemap_res.text:
            for loc in _sitemap_urls(sitemap_res.text):
                enqueue(loc, 1)

        semaphore = asyncio.Semaphore(concurrency)

        async def visit(url: str, depth: int) -> tuple[Optional[Page], list[str]]:
            async with semaphore:
                res = await fetch(url)
            if res.status != 200:
                result.skipped.append({"url": url, "reason": f"http {res.status}"})
                return None, []
            if "html" not in res.content_type.lower():
                result.skipped.append({"url": url, "reason": "not html"})
                return None, []
            final = normalize_url(res.final_url or url)
            if final != url and final in seen:
                return None, []
            seen.add(final)
            title, text = extract_text(res.text, final)
            links = extract_links(res.text, final) if depth < max_depth else []
            if len(text.split()) < MIN_WORDS:
                result.skipped.append({"url": final, "reason": "too little text"})
                return None, links
            return Page(url=final, title=title, text=text, depth=depth), links

        while queue and len(result.pages) < max_pages:
            batch, queue = queue[:concurrency], queue[concurrency:]
            allowed = []
            for u, d in batch:
                if robots.can_fetch(USER_AGENT, u):
                    allowed.append((u, d))
                else:
                    result.skipped.append({"url": u, "reason": "robots.txt"})
            if not allowed:
                continue
            outcomes = await asyncio.gather(*(visit(u, d) for u, d in allowed))
            for (u, d), (page, links) in zip(allowed, outcomes):
                if page is not None and len(result.pages) < max_pages:
                    result.pages.append(page)
                for link in links:
                    enqueue(link, d + 1)
    finally:
        if client is not None:
            await client.aclose()

    logger.info(f"Crawled {start}: {len(result.pages)} pages, {len(result.skipped)} skipped")
    return result
