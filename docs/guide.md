# Operator's Guide

Everything you need to configure, integrate, run and deploy CHAT-API. The
README covers the five-minute version; this document is the reference.

- [Configuration](#configuration)
- [Multi-tenant mode](#multi-tenant-mode)
- [System prompts](#system-prompts)
- [LLM providers](#llm-providers)
- [Cost controls](#cost-controls)
- [Frontend integration](#frontend-integration)
- [Knowledge base (RAG)](#knowledge-base-rag)
- [Local development](#local-development)
- [Deploying to Cloud Run](#deploying-to-cloud-run)
- [Troubleshooting](#troubleshooting)

## Configuration

All settings are environment variables, read once at startup by
`app/config.py`. Locally they come from `.env`; on Cloud Run from the service
definition (see `deployments/`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `TENANTS_FILE` | – | Path to a tenants YAML file; enables [multi-tenant mode](#multi-tenant-mode) |
| `LLM_PROVIDER` | `openai` | `openai`, `anthropic` or `gemini` (single-tenant mode) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | – / `gpt-4o-mini` | OpenAI credentials and model |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | – / `claude-3-haiku-20240307` | Anthropic credentials and model |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | – / `gemini-3.1-flash-lite` | Google Gemini credentials and model |
| `MAX_TOKENS` | `512` | Maximum tokens per reply |
| `TEMPERATURE` | `0.7` | Sampling temperature (OpenAI and Anthropic only) |
| `SYSTEM_PROMPT_EN` / `SYSTEM_PROMPT_ZH` | built-in | Site-specific prompts, see below |
| `SYSTEM_PROMPT_FILE` | – | JSON file `{"en": "...", "zh": "..."}`; wins over the two above |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma or semicolon separated allow-list |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW` | `20` / `60` | Requests per IP per window (seconds) |
| `MAX_INPUT_LENGTH` | `500` | Characters allowed in the latest user message |
| `MAX_MESSAGES_PER_SESSION` | `20` | Longest conversation accepted |
| `MAX_CONTEXT_MESSAGES` | `10` | Messages actually sent to the model |
| `PINECONE_API_KEY` / `PINECONE_INDEX` | – / `chat-api-rag` | Enables the knowledge base when the key is set |
| `GCS_BUCKET` / `GCS_PREFIX` | `chat-api-rag-documents` / `documents` | Where uploaded source documents are kept |
| `RAG_DEFAULT_TOP_K` / `RAG_MIN_SCORE_THRESHOLD` | `5` / `0.1` | Retrieval depth and cut-off |
| `APP_NAME`, `APP_ENV`, `LOG_LEVEL`, `PORT` | – | Housekeeping |

## Multi-tenant mode

Set `TENANTS_FILE` to serve several websites from one deployment. Each tenant
has its own origins, model, prompts, optional knowledge-base namespace and
limit overrides:

```yaml
default: studio
tenants:
  studio:
    name: Art Studio
    origins: [https://studio.example, https://www.studio.example]
    llm: { provider: gemini, model: gemini-3.1-flash-lite, api_key_env: STUDIO_GEMINI_API_KEY }
    prompts:
      en: |
        You are the Studio assistant ...
      zh: |
        您是工作室助手 ...
    knowledge_base: { namespace: studio }
    limits: { rate_limit_requests: 30, max_input_length: 800 }
  consultancy:
    origins: [https://consultancy.example]
    llm: { provider: openai, model: gpt-4o-mini, api_key_env: CONSULTANCY_OPENAI_API_KEY }
    prompts: { en: "You are the Consultancy assistant ..." }
```

How a request finds its tenant:

1. `site` in the JSON body (chat) or `?site=` (knowledge-base endpoints).
   Unknown id → `404`.
2. Otherwise the `Origin` header, matched against every tenant's origins.
   An origin nobody claims → `403`. Browsers always send `Origin` on
   cross-site requests, so widgets need no configuration.
3. Otherwise the `default` tenant (server-to-server calls, curl).

What is per tenant: prompts, provider and model, API key (named by
`api_key_env`, filled from the environment), `rate_limit_requests`,
`max_input_length`, Pinecone namespace and storage folder. What stays shared:
the Pinecone index and bucket, the rate-limit window, conversation and context
limits, logging. The CORS allow-list is the union of all tenant origins.

Startup validates the file: unknown providers, an origin claimed twice, a
missing default, or an empty key variable stop the container before it serves
traffic, so mistakes surface at deploy time.

Without `TENANTS_FILE` the service builds one implicit tenant from the plain
variables, which is the mode the rest of this guide describes.

## System prompts

The prompt is the product. Resolution order:

1. `SYSTEM_PROMPT_FILE` – a JSON file with `en` and `zh` keys
2. `SYSTEM_PROMPT_EN` and `SYSTEM_PROMPT_ZH` environment variables
3. Generic built-in defaults in `app/prompts/system_prompts.py`

The built-in defaults know nothing about your organisation on purpose. Put
site knowledge in a deployment config (`deployments/<site>/env.yaml`) using
YAML block scalars, which keeps multi-line prompts readable and diff-able.

A prompt that works well has five parts: who the assistant is, verified facts
about the organisation, what it may answer, what it must redirect to a human
(pricing, schedules, legal), and style rules (length, language, no markdown
if your widget renders plain text).

## LLM providers

`app/services/llm_service.py` builds a LangChain chat model from the
configured provider. Adding one is three edits: extend the `Literal` in
`config.py`, add the key and model settings, add a branch in `get_llm()`.

Notes from production:

- Gemini Flash-Lite models do not "think" by default, so `MAX_TOKENS=512` is
  enough. Non-lite Gemini 3 models spend most of that budget on thinking and
  return truncated answers unless `MAX_TOKENS` is raised a lot.
- Temperature is intentionally not passed to Gemini; Google recommends the
  model default there.
- Streamed chunks are passed through `chunk_text()`, which forwards only text
  blocks. Provider-specific blocks (thinking, tool calls) never reach the
  browser.

## Cost controls

Four layers, all configurable, all on by default:

1. **Rate limit** per client IP (honours `X-Forwarded-For`). Returns `429`
   with a `Retry-After` header.
2. **Input limits**: message length and conversation length are rejected with
   `400` before any model call.
3. **Context truncation**: only the last `MAX_CONTEXT_MESSAGES` messages are
   sent to the model, so cost per request has a ceiling.
4. **Disconnect detection**: the streaming endpoint checks
   `request.is_disconnected()` between tokens and stops generating when the
   visitor closes the widget.

The limiter is in-memory and per instance; with several Cloud Run instances
each keeps its own counters. That is acceptable for abuse protection on a
marketing site, not for billing-grade quotas.

## Frontend integration

### Streaming endpoint

`POST /chat/stream` with:

```json
{
  "session_id": "1736789012345-a8b3c9d",
  "messages": [
    { "role": "user", "content": "Hi there!" },
    { "role": "assistant", "content": "Hello! How can I help?" },
    { "role": "user", "content": "What do you offer?" }
  ],
  "locale": "en",
  "page_url": "https://example.com/pricing"
}
```

The response is `text/event-stream`:

```
event: token
data: We

event: token
data:  offer

event: done
data:
```

`error` events carry a message in `data`. Lines starting with `:` are
keep-alive pings and must be ignored. Per the SSE spec, strip exactly one
space after `data:`; a token consisting of a single space is legitimate.

Send the full history each time (the server truncates), generate a fresh
`session_id` when the visitor starts over, and never include `system`
messages: the server drops them. In multi-tenant mode the browser's `Origin`
selects the site; add `"site": "<id>"` only for non-browser callers.

### Minimal browser client

```javascript
export async function streamChat({ apiUrl, sessionId, messages, locale = 'en', onToken, onDone, onError, signal }) {
  const res = await fetch(`${apiUrl}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, messages, locale, page_url: location.href }),
    signal,
  });
  if (!res.ok) { onError?.(`HTTP ${res.status}`); return; }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = 'token';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n');
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (line === '' || line.startsWith(':')) continue;
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
        if (event === 'done') { onDone?.(); return; }
        continue;
      }
      if (line.startsWith('data:')) {
        let data = line.slice(5);
        if (data.startsWith(' ')) data = data.slice(1);
        if (event === 'token') onToken?.(data);
        else if (event === 'error') { onError?.(data); return; }
      }
    }
  }
  onDone?.();
}
```

Pass an `AbortController.signal` and call `abort()` for a Stop button; the
server notices the disconnect and stops generating.

Two production widgets built on this client, both MIT-licensed alongside their
sites: a React + Vite one at
[UAACC/ah-studio](https://github.com/UAACC/ah-studio/tree/main/src/components/ChatWidget)
and a Next.js one at
[UAACC/orctech-website](https://github.com/UAACC/orctech-website/tree/master/src/components/ChatWidget).

### Non-streaming endpoint

`POST /chat` takes the same body and returns `{"reply": "...", "session_id": "..."}`.
Useful for server-to-server calls and tests.

### Errors

| Status | Meaning |
|--------|---------|
| `400` | Message or conversation too long |
| `422` | Malformed body (unknown role, unsupported locale) |
| `429` | Rate limited; wait `Retry-After` seconds |
| `500` | Upstream failure on the non-streaming endpoint (streaming reports via an `error` event) |

## Knowledge base (RAG)

Optional. When `PINECONE_API_KEY` is set, every chat request first searches a
Pinecone index using Pinecone's integrated embeddings (no separate embedding
API) and prepends the best chunks to the system prompt inside a
`<retrieved_context>` block. Source files are kept in Google Cloud Storage so
they can be listed and deleted later. If retrieval fails the request continues
without context and a warning is logged.

All document endpoints take an optional `?site=<id>` (or infer the tenant
from `Origin`); a tenant without a `knowledge_base` block gets `400`.

| Endpoint | Purpose |
|----------|---------|
| `POST /rag/documents/upload` | Multipart upload (PDF, Markdown, text); chunks and indexes the file |
| `GET /rag/documents` | List indexed documents |
| `GET /rag/documents/{id}` | Details for one document |
| `DELETE /rag/documents/{id}` | Remove a document and its vectors |
| `GET /rag/status` | Connectivity of Pinecone and GCS |
| `POST /chat/rag`, `POST /chat/rag/stream` | Chat with explicit `top_k` / `min_score` and the retrieved context echoed back |

Chunking is token-based (`RAG_CHUNK_SIZE` 512, overlap 64). Keep knowledge
files factual and short; the prompt already carries the tone.

## Local development

With Python 3.11+:

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                # add one provider key
uvicorn app.main:app --reload --port 8080
pytest
```

Without Python, the Docker image is the environment:

```bash
docker build -t chat-api .
docker run --rm -p 8080:8080 -e LLM_PROVIDER=gemini -e GEMINI_API_KEY=... chat-api
docker run --rm -v "$PWD:/src" -w /src chat-api sh -c "pip install -q pytest pytest-asyncio && pytest -q"
```

The test suite never calls a real provider: `tests/conftest.py` swaps the
model for a canned one, so it runs in a couple of seconds and offline.

Interactive API docs are served at `/docs`.

## Deploying to Cloud Run

One-time, per Google Cloud project:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    secretmanager.googleapis.com artifactregistry.googleapis.com

printf '%s' "$GEMINI_KEY" | gcloud secrets create gemini-api-key --data-file=-
gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

Per site, from the repo root:

```bash
gcloud run deploy <service> --region=us-central1 --source=. --allow-unauthenticated \
    --env-vars-file=deployments/<site>/env.yaml \
    --update-secrets="GEMINI_API_KEY=gemini-api-key:latest"
```

Write secrets with `printf '%s'` (no trailing newline); a newline inside an
API key is the single most common cause of "invalid key" errors. Use
`--update-*` flags rather than `--set-*` when touching an existing service, or
you will silently remove the variables you did not mention.

`--min-instances 0` is free when idle at the cost of a cold start of several
seconds; `--min-instances 1` removes it for a few dollars a month.

Logs: `gcloud run services logs read <service> --region us-central1 --limit 50`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Browser shows a CORS error | Site origin missing from `CORS_ORIGINS` | Add it (scheme + host, no path) and redeploy |
| `error` event: `credit_balance_exhausted` / `insufficient_quota` | Provider account out of credit | Top up, or switch provider |
| `error` event: `RESOURCE_EXHAUSTED ... prepayment credits` (Gemini) | Key belongs to a project on a paid billing account with no balance | Create the key in a project with no billing account to use the free tier |
| Gemini `503 UNAVAILABLE` | Model under load; the client retries a few times | Pick a different Flash model in `GEMINI_MODEL` |
| Replies cut off mid-sentence | `MAX_TOKENS` too low, or a thinking model | Raise `MAX_TOKENS` or use a Flash-Lite model |
| First request takes 10+ seconds | Cold start | `--min-instances 1` |
| Knowledge base returns 0 chunks | Index empty or `RAG_MIN_SCORE_THRESHOLD` too high | Check `GET /rag/documents`, lower the threshold |
