# Provider fallback

Status: approved 2026-09-06

## Goal

Keep answering when the primary model is overloaded, rate limited, out of
credit or withdrawn, by trying the tenant's backup models in order.

## Configuration

```yaml
tenants:
  studio:
    llm:
      provider: gemini
      model: gemini-3.1-flash-lite
      api_key_env: STUDIO_GEMINI_API_KEY
    fallbacks:
      - provider: gemini
        model: gemini-3.5-flash-lite
        api_key_env: STUDIO_GEMINI_API_KEY
      - provider: openai
        model: gpt-4o-mini
        api_key_env: STUDIO_OPENAI_API_KEY
        max_tokens: 1024
```

Each entry has the same fields as `llm` (its own key variable and token
budget, so a "thinking" backup can get a larger budget). Keys are resolved at
startup like the primary's. `fallbacks` is optional; single-tenant mode has
none.

## Behaviour

Candidates are `[llm, *fallbacks]`. For a request:

1. Try the first candidate. If it fails **before any text has been produced**
   with a fallback-worthy error, log a warning and try the next.
2. Once a token has been streamed, the request stays on that model: a failure
   mid-stream is reported as today (an `error` event), never spliced with a
   second model's continuation.
3. If every candidate fails, the last error is raised.

Fallback-worthy errors: HTTP status 408, 409, 425, 429, 5xx; exception
classes or messages indicating rate limits, overload, unavailability, model
not found, quota or credit exhaustion, connection failures. Anything else
(invalid request, content policy, bad key) raises immediately because a
different model will not help.

Models are still cached per (tenant, candidate); provider retries inside the
SDKs are left as they are.

## Observability

Startup logs each tenant's candidate chain. A fallback logs
`tenant=<id> fell back from <provider>/<model> to <provider>/<model>: <error>`.

## Testing

`tests/test_fallback.py` with canned models that raise on demand: falls back
on a retryable error, does not fall back on a non-retryable one, does not
fall back after the first token, exhausts the chain and raises the last
error, non-streaming path behaves the same, classification table for
`should_fall_back`, config loading and key resolution for fallbacks.

## Rollout

Both production tenants get `gemini-3.5-flash-lite` as first fallback and
`gemini-3.5-flash` (with `max_tokens: 2048`) as second, on the same key.
