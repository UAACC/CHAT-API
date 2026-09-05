# Multi-tenant mode

Status: approved 2026-09-05

## Goal

Serve several websites from one deployment. Each site (tenant) gets its own
prompts, model, allowed origins and knowledge-base namespace, selected per
request. A deployment without tenant configuration keeps today's single-site
behaviour unchanged.

## Non-goals

- Per-tenant billing or usage quotas
- A management UI or API for editing tenants at runtime
- Authentication for the knowledge-base endpoints (unchanged from today)

## Tenant resolution

Resolution order for every request:

1. Explicit `site` field in the JSON body (chat endpoints) or `site` query
   parameter (knowledge-base endpoints). Unknown id → `404 unknown site`.
2. `Origin` header matched, scheme and host, against every tenant's `origins`.
   No match → `403 origin not allowed`.
3. No `Origin` and no `site` (server-to-server, curl, tests) → the tenant named
   by `default`.

The tenant is provided to route handlers through a FastAPI dependency
(`get_tenant`). Handlers never read tenant configuration directly.

## Configuration

`TENANTS_FILE` points to a YAML file:

```yaml
default: ah-studio
tenants:
  ah-studio:
    name: A.H. Studio
    origins:
      - https://allisonhe.ca
      - https://www.allisonhe.ca
      - http://localhost:5173
    llm:
      provider: gemini              # openai | anthropic | gemini
      model: gemini-3.1-flash-lite
      api_key_env: AH_STUDIO_GEMINI_API_KEY
      max_tokens: 512               # optional, default 512
      temperature: 0.7              # optional, ignored by gemini
    prompts:
      en: |
        ...
      zh: |
        ...
    knowledge_base:                 # optional; absent = prompt only
      namespace: ah-studio          # Pinecone namespace in the shared index
    limits:                         # optional overrides of the global values
      rate_limit_requests: 20
      max_input_length: 500
```

Rules:

- `api_key_env` names an environment variable; the file never contains keys.
  A tenant whose variable is empty fails validation at startup, so a missing
  secret is caught by the deploy, not by the first visitor.
- The shared settings that stay global: `PINECONE_API_KEY`, `PINECONE_INDEX`,
  `GCS_BUCKET`, `RATE_LIMIT_WINDOW`, `MAX_MESSAGES_PER_SESSION`,
  `MAX_CONTEXT_MESSAGES`, `RAG_*`, logging and server settings.
- Validation is a pydantic model (`TenantConfig`, `TenantsFile`). Duplicate
  origins across tenants are rejected.

When `TENANTS_FILE` is unset, an implicit single tenant with id `default` is
built from the existing environment variables (`LLM_PROVIDER`, `*_API_KEY`,
`*_MODEL`, `SYSTEM_PROMPT_*`, `CORS_ORIGINS`, `PINECONE_*`). Every current
deployment and every existing test therefore keeps working without changes.

## Components

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `app/tenants.py` | Load and validate the file, build the implicit tenant, `get_tenant` dependency, `TenantRegistry` (id → tenant, origin → tenant) | `config`, `pyyaml` |
| `app/services/llm_service.py` | `get_llm(tenant)` builds and caches one model per tenant; message builders take the tenant's prompts | `tenants` |
| `app/prompts/system_prompts.py` | Generic defaults only; `get_system_prompt(locale, tenant)` returns the tenant prompt or the default | – |
| `app/routes/chat.py` | Adds `tenant = Depends(get_tenant)`; validation, truncation, retrieval and generation all take the tenant | `tenants`, services |
| `app/routes/rag.py` | `site` query parameter; documents are stored under `<gcs_prefix>/<tenant id>/` and indexed in the tenant namespace | `tenants` |
| `app/middleware/rate_limit.py` | Key is `<tenant id>:<client ip>`; limit taken from the tenant, window from global settings | `tenants` |
| `app/main.py` | Builds the registry at startup; CORS allow-list is the union of all tenant origins | `tenants` |
| `app/routes/health.py` | Adds `tenants: [ids]` to `/health` | `tenants` |

Module-level `settings = get_settings()` captures in the routes and the rate
limiter are replaced with dependency injection so tests can vary
configuration without reloading modules.

## Data flow

```
request ──► get_tenant ──► rate limit (tenant:ip) ──► validate (tenant limits)
        ──► truncate ──► retrieve (namespace = tenant.knowledge_base.namespace)
        ──► build messages (tenant prompt) ──► get_llm(tenant) ──► stream
```

## Error handling

| Situation | Response |
|-----------|----------|
| Unknown `site` id | `404 {"detail": "unknown site"}` |
| `Origin` present but matches no tenant | `403 {"detail": "origin not allowed"}` (CORS preflight already fails for browsers; this covers non-browser clients) |
| Tenant file missing, unparsable or invalid | Startup fails with a clear message; the container never becomes healthy |
| Tenant API key variable empty | Startup fails naming the variable |
| Knowledge base configured but `PINECONE_API_KEY` unset | Startup warning; chat continues without retrieval (existing behaviour) |

## Testing

New `tests/test_tenants.py` with a two-tenant fixture file:

- resolves by `site` field, by `Origin`, falls back to `default`
- unknown `site` → 404; unmatched `Origin` → 403
- tenants get their own prompt (system message content differs) and own model
  (provider/model taken from the tenant, keyed API key env var)
- CORS allow-list is the union; preflight succeeds for both sites
- rate limit is isolated per tenant for the same IP
- duplicate origins and empty key variables fail validation
- no `TENANTS_FILE` → implicit tenant mirrors env settings (existing tests
  cover the behaviour; one explicit test asserts the registry shape)

## Rollout

1. Land the code with the implicit single-tenant path; existing deployments
   untouched.
2. Write `deployments/tenants.yaml` with both sites (prompts move out of the
   two `env.yaml` files, which are deleted). `deployments/env.yaml` keeps the
   shared, non-secret settings.
3. Redeploy `chat-api` with `TENANTS_FILE=deployments/tenants.yaml`, both
   Gemini secrets mounted as `AH_STUDIO_GEMINI_API_KEY` and
   `ORCTECH_GEMINI_API_KEY`, plus `PINECONE_API_KEY`. Cloud Build must include
   `deployments/` (adjust `.gcloudignore` / `.dockerignore`).
4. Verify both sites against `chat-api` with their `Origin` headers.
5. Point orctech.ca's `NEXT_PUBLIC_API_URL` at `chat-api`, verify in a
   browser, then delete the `orctech-chat-api` service.
