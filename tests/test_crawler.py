"""Site crawler against an in-memory website."""

import pytest

from app.services import crawler
from app.services.crawler import FetchResult, crawl_site, document_id_for, extract_text, normalize_url


def page(title, body, links=()):
    nav = '<nav><a href="/">Home</a><a href="/about">About</a></nav>'
    anchors = "".join(f'<a href="{href}">link</a>' for href in links)
    filler = " ".join(["word"] * 60)
    return (
        f"<html><head><title>{title}</title><style>.x{{}}</style></head><body>"
        f"<header>Site header</header>{nav}<main><h1>{title}</h1><p>{body}</p><p>{filler}</p>{anchors}</main>"
        f"<footer>Footer text</footer><script>alert(1)</script></body></html>"
    )


SITE = {
    "https://example.com/": page("Home", "Welcome home.", ["/about", "/team?utm_source=x", "https://other.example/leak", "/brochure.pdf", "mailto:a@b.c", "#top", "/private/secret"]),
    "https://example.com/about": page("About", "About us.", ["/team", "/deep1"]),
    "https://example.com/team": page("Team", "Our team.", []),
    "https://example.com/deep1": page("Deep one", "Level two.", ["/deep2"]),
    "https://example.com/deep2": page("Deep two", "Level three.", ["/deep3"]),
    "https://example.com/deep3": page("Deep three", "Level four.", []),
    "https://example.com/private/secret": page("Secret", "Hidden.", []),
    "https://example.com/thin": "<html><body><main><p>tiny</p></main></body></html>",
    "https://example.com/robots.txt": "User-agent: *\nDisallow: /private/\n",
    "https://example.com/sitemap.xml": "<urlset><url><loc>https://example.com/thin</loc></url><url><loc>https://example.com/team</loc></url></urlset>",
    "https://example.com/brochure.pdf": "%PDF-1.4",
}


async def fake_fetch(url: str) -> FetchResult:
    if url not in SITE:
        return FetchResult(status=404)
    body = SITE[url]
    if url.endswith(".pdf"):
        return FetchResult(status=200, content_type="application/pdf", text="", final_url=url)
    if url.endswith(".txt"):
        return FetchResult(status=200, content_type="text/plain", text=body, final_url=url)
    if url.endswith(".xml"):
        return FetchResult(status=200, content_type="application/xml", text=body, final_url=url)
    return FetchResult(status=200, content_type="text/html; charset=utf-8", text=body, final_url=url)


class TestNormalization:
    def test_drops_fragment_and_tracking_params(self):
        assert normalize_url("HTTPS://Example.com/Team?utm_source=x&page=2#top") == "https://example.com/Team?page=2"

    def test_empty_path_becomes_root(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_document_id_is_stable_across_variants(self):
        assert document_id_for("https://example.com/about#x") == document_id_for("https://EXAMPLE.com/about?utm_medium=y")
        assert document_id_for("https://example.com/about") != document_id_for("https://example.com/team")
        assert document_id_for("https://example.com/about").startswith("url-")


class TestExtraction:
    def test_keeps_main_drops_chrome(self):
        title, text = extract_text(SITE["https://example.com/"], "https://example.com/")
        assert title == "Home"
        assert "Welcome home." in text
        assert "Site header" not in text
        assert "Footer text" not in text
        assert "alert(1)" not in text
        assert "About" not in text.split("Welcome")[0]  # nav links removed

    def test_headings_become_lines(self):
        _, text = extract_text("<body><h2>Prices</h2><p>From ten dollars.</p><h2>Hours</h2><p>Nine to five.</p></body>", "u")
        assert text.split("\n\n")[0] == "Prices"
        assert "Hours" in text

    def test_title_falls_back_to_url(self):
        title, _ = extract_text("<body><p>no title here</p></body>", "https://example.com/x")
        assert title == "https://example.com/x"


class TestCrawl:
    async def test_crawls_same_site_and_respects_rules(self):
        result = await crawl_site("https://example.com", max_pages=10, max_depth=3, fetch=fake_fetch)
        urls = {p.url for p in result.pages}

        assert "https://example.com/" in urls
        assert "https://example.com/about" in urls
        assert "https://example.com/team" in urls  # tracking param stripped, visited once
        assert not any("other.example" in u for u in urls)  # other host
        assert "https://example.com/private/secret" not in urls  # robots.txt

        reasons = {s["url"]: s["reason"] for s in result.skipped}
        assert reasons["https://example.com/private/secret"] == "robots.txt"
        assert reasons["https://example.com/brochure.pdf"] == "not html"
        assert reasons["https://example.com/thin"] == "too little text"  # sitemap seeded, then skipped

    async def test_max_depth(self):
        result = await crawl_site("https://example.com", max_pages=50, max_depth=1, fetch=fake_fetch)
        urls = {p.url for p in result.pages}
        assert "https://example.com/about" in urls  # depth 1
        assert "https://example.com/deep1" not in urls  # depth 2

    async def test_max_pages(self):
        result = await crawl_site("https://example.com", max_pages=2, max_depth=3, fetch=fake_fetch)
        assert len(result.pages) == 2

    async def test_hard_cap(self, monkeypatch):
        monkeypatch.setattr(crawler, "MAX_PAGES_HARD_CAP", 1)
        result = await crawl_site("https://example.com", max_pages=999, max_depth=3, fetch=fake_fetch)
        assert len(result.pages) == 1

    async def test_each_page_visited_once(self):
        calls = []

        async def counting(url):
            calls.append(url)
            return await fake_fetch(url)

        await crawl_site("https://example.com", max_pages=50, max_depth=3, fetch=counting)
        html_calls = [c for c in calls if not c.endswith((".txt", ".xml"))]
        assert len(html_calls) == len(set(html_calls))

    async def test_unreachable_start(self):
        async def down(url):
            return FetchResult(status=0)

        result = await crawl_site("https://example.com", fetch=down)
        assert result.pages == []
        assert result.skipped[0]["reason"] == "http 0"
