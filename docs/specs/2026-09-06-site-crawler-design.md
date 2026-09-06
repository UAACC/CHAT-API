# Site crawler for the knowledge base

Status: approved 2026-09-06

## Goal

Build or refresh a tenant's knowledge base from its website with one request,
instead of hand-maintaining documents. Also close the gap that knowledge-base
mutations are unauthenticated.

## API

`POST /rag/crawl?site=<id>` with `Authorization: Bearer <ADMIN_TOKEN>`:

```json
{ "url": "https://orctech.ca", "max_pages": 30, "max_depth": 3 }
```

Runs synchronously (small marketing sites finish in seconds; hard cap 200
pages, 3 s connect / 10 s read per page, 4 concurrent fetches) and returns:

```json
{
  "site": "orctech",
  "start_url": "https://orctech.ca",
  "pages_indexed": 6,
  "chunks": 41,
  "pages": [ { "url": "https://orctech.ca/", "title": "OrcTech", "chunks": 9, "document_id": "url-…" } ],
  "skipped": [ { "url": "https://orctech.ca/brochure.pdf", "reason": "not html" } ]
}
```

## Crawling rules

- Same host as the start URL (a bare/`www.` pair counts as one host); other
  hosts, `mailto:`, `tel:`, fragments and non-HTML responses are skipped.
- `robots.txt` is fetched once and honoured for user agent `chat-api-crawler`.
- URLs are normalised (scheme+host lower-cased, fragment dropped, tracking
  query parameters `utm_*`, `fbclid`, `gclid` removed, trailing slash kept as
  served) and visited once.
- Breadth-first from the start URL; `/sitemap.xml` (if present) seeds the
  queue at depth 1.
- Fetching is injectable (`fetch(url) -> FetchResult`) so the crawler is unit
  tested against an in-memory site.

## Extraction

`bs4` (`html.parser`, no lxml). Remove `script`, `style`, `noscript`,
`template`, `nav`, `header`, `footer`, `aside`, `form`, and elements with
`role="navigation"` or `aria-hidden="true"`. Prefer `<main>`, then
`<article>`, then `<body>`. Headings become their own lines; block elements
are separated by blank lines; whitespace is collapsed. Pages with fewer than
40 words of text are skipped (empty SPA shells, redirects).

Limitation, documented: client-rendered sites (a Vite SPA such as
allisonhe.ca) return an empty shell to a plain fetch and yield nothing. Those
sites keep using document upload. A headless browser is out of scope.

## Indexing

One document per page. `document_id = "url-" + sha1(normalised url)[:24]`, so
re-crawling replaces the page's document instead of accumulating copies. The
extracted text is stored as `text/plain` with filename `<path or index>.txt`
under the tenant's storage prefix; chunks carry `url` and `title` metadata in
the tenant's namespace. `process_document` gains an `extra_metadata` argument
for this.

## Admin token

`ADMIN_TOKEN` setting. When set, `POST /rag/documents/upload`,
`DELETE /rag/documents/{id}` and `POST /rag/crawl` require
`Authorization: Bearer <token>` (constant-time comparison) and answer `401`
otherwise. When unset the endpoints stay open and startup logs a warning, so
existing single-tenant deployments are not broken. Read endpoints stay open.
Production mounts the token from Secret Manager.

## Testing

- `tests/test_crawler.py`: in-memory site; same-host filter, robots, depth and
  page caps, dedupe of normalised URLs, sitemap seeding, extraction rules,
  minimum-text skip, deterministic ids.
- `tests/test_rag_routes.py`: crawl endpoint with mocked crawler and document
  service (summary shape, replacement of existing docs), admin token
  enforcement on the three mutating endpoints, open behaviour when unset.
- Manual: crawl orctech.ca in production, ask a question answered only by
  page content, confirm the retrieved context.

## Rollout

1. Land code; deploy with `ADMIN_TOKEN` secret mounted.
2. Crawl orctech.ca into the `orctech` tenant (add a `knowledge_base` block for
   it in `tenants.yaml` first).
3. Re-upload a cleaned `deployments/ah-studio/knowledge_base.md` without the
   prompt-template sections.
