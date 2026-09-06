# Knowledge console

Status: approved 2026-09-06

## Goal

A page where the site owner can see what the assistant knows, test what a
question retrieves, check the answer it produces, and add or correct
knowledge in seconds, without curl or YAML.

## Page

`GET /admin`, a single HTML file served by the API (no framework). The
operator pastes `ADMIN_TOKEN` once; it is kept in `sessionStorage` and sent
as `Authorization: Bearer` on every call. A tenant selector is filled from
`/health`.

Sections:

1. **Documents**: filename, source URL, size, chunk count, date; expand to
   read every chunk; delete; upload a file; crawl a URL and show the summary.
2. **Retrieval test**: a question → the top chunks with scores; chunks below
   the tenant's threshold are shown greyed with "not used".
3. **Test answer**: the same question through `POST /chat/rag`, showing the
   reply and the context it actually used.
4. **Notes**: a textarea of curated facts, one paragraph per note. Saving
   re-indexes them as the `notes` document, one chunk per paragraph.

## Endpoints

All under `/rag` require the admin token when `ADMIN_TOKEN` is set,
including the existing list and detail reads; `/rag/status` stays open for
monitoring. New:

| Endpoint | Purpose |
|----------|---------|
| `GET /rag/documents/{id}/chunks?site=` | Every chunk of a document (index, text, url) |
| `GET /rag/search?site=&q=&top_k=` | Retrieval only: hits with score and whether they clear the threshold |
| `GET /rag/notes?site=` | Current notes text |
| `PUT /rag/notes?site=` `{"text": "..."}` | Replace the notes document; returns note count |
| `GET /admin` | The console |

Notes are stored as `notes.md` under the tenant's storage prefix and indexed
as records `notes_<i>` with `filename: notes.md`, `title: Notes`, so they
appear in retrieval like any other chunk and can be deleted like any
document.

## Testing

Route tests with mocked storage and vector services: chunks listing, search
result shape and threshold flag, notes round trip (delete + upload + upsert,
one record per paragraph, empty text clears), admin page served, `/rag/status`
open while `/rag/documents` requires the token. Manual: Playwright on the
live console: sign in, browse chunks, run a retrieval test, add a note and
retrieve it.
