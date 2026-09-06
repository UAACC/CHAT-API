# Changelog

All notable changes to this project. Dates are release dates on the
production service.

## 1.5.0 — 2026-09-06

### Added
- **Knowledge console** at `/admin`: browse every stored chunk, run a
  retrieval test with scores and the threshold marker, check the answer with
  the context it used, upload, crawl, delete, and curate notes that index one
  chunk per paragraph.
- Endpoints `GET /rag/documents/{id}/chunks`, `GET /rag/search`,
  `GET`/`PUT /rag/notes`.
- **Provider fallback**: tenants list backup models; a model that fails before
  producing text with an overload, rate-limit, quota, withdrawn-model, 5xx or
  connection error is skipped for the next one.
- Theme toggle (auto / dark / light) on the console.

### Changed
- Console and widget demo page redesigned: sidebar shell, IBM Plex Sans and
  JetBrains Mono, toasts, skeleton loaders, similarity bars, inline delete
  confirmation.
- All `/rag/*` endpoints except `/rag/status` now require `ADMIN_TOKEN`.

## 1.4.0 — 2026-09-05

### Added
- **Site crawler**: `POST /rag/crawl` builds a knowledge base from a website
  (same-host, `robots.txt`, sitemap seeding, one document per page with
  URL-derived ids so re-crawls refresh in place).
- `ADMIN_TOKEN` bearer auth for endpoints that change a knowledge base.
- Chunk metadata carries the source `url` and `title`.

### Fixed
- Retrieval returned nothing on pinecone>=10, which serialises hits as
  `id_`/`score_`; both spellings are now accepted.
- An explicit `min_score` of `0` is honoured instead of falling back to the
  default.

## 1.3.0 — 2026-09-05

### Added
- **Embeddable widget**: `GET /widget.js`, a dependency-free chat panel in a
  Shadow DOM configured by `data-*` attributes; streaming, stop, retry, EN/ZH
  toggle, persistence, themes. Served gzipped with ETag revalidation.
- `GET /widget/demo` page with a site picker.
- Same-host origins resolve to the default tenant so pages served by the API
  can call it.

## 1.2.0 — 2026-09-05

### Added
- **Multi-tenant mode**: `TENANTS_FILE` describes many sites (origins, model,
  prompts, knowledge-base namespace, limit overrides); requests resolve to a
  tenant by explicit `site`, then `Origin`, then the default. Per-tenant model
  cache, rate-limit key and Pinecone namespace; CORS allow-list is the union
  of tenant origins.
- App factory (`create_app`) so tests can build apps with their own tenants.

### Changed
- `deployments/tenants.yaml` and `env.yaml` replace the per-site env files;
  both production sites now run on one Cloud Run service.

## 1.1.0 — 2026-09-05

### Added
- pytest suite (offline, canned model) and GitHub Actions CI with a Docker
  smoke test.
- Generic default prompts; site prompts move to deployment configuration.
- English README, operator's guide, deployment guide, MIT license,
  `pyproject.toml`.

### Removed
- Unused OpenAI embedding service and stray files.

## 1.0.0 — 2026-09-04

### Added
- Google Gemini provider alongside OpenAI and Anthropic.
- Knowledge-base retrieval made optional: no Pinecone key means prompt-only
  answers; retrieval errors no longer fail the chat.
- Second production deployment (orctech.ca) from the same codebase.

## 0.x — 2026-01 to 2026-02

- Initial FastAPI backend with SSE streaming, OpenAI and Anthropic providers,
  bilingual prompts, rate limiting and cost protection, Cloud Run deployment.
- Pinecone-backed knowledge base with integrated embeddings and Google Cloud
  Storage for source documents.
