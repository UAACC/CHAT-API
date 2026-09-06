# Security

## Reporting a vulnerability

Email the maintainer through the address on the GitHub profile, or open a
private security advisory on this repository. Please include steps to
reproduce. You will get an acknowledgement within a few days, and a fix or a
mitigation plan before any public disclosure.

## What the service protects, and how

| Surface | Protection |
|---------|------------|
| Chat endpoints | Per-tenant, per-IP rate limiting; input and conversation length limits; only origins listed for a tenant are accepted (`403` otherwise) |
| Knowledge-base endpoints and console | `ADMIN_TOKEN` bearer auth with constant-time comparison; the app warns at startup when it is unset |
| API keys | Never in the repository or the tenants file; read from environment variables that Cloud Run fills from Secret Manager |
| Crawler | Same-host only, honours `robots.txt`, bounded pages and depth, short timeouts |
| Widget | Escapes all model output before rendering; links open with `rel="noopener noreferrer"`; no third-party scripts |

## Things to know when deploying

- The widget's `data-*` attributes and the chat endpoints are public by
  design; do not put secrets in prompts or notes that visitors must not see.
  Everything in a tenant's knowledge base can be surfaced by a reply.
- The rate limiter is in-memory and per instance. For billing-grade quotas
  put a gateway with shared counters in front of the service.
- Write endpoints are open until `ADMIN_TOKEN` is set. Set it before
  exposing the service to the internet.
- Rotate `ADMIN_TOKEN` and provider keys by adding a new secret version and
  redeploying; the deploy commands in `deployments/README.md` always mount
  `latest`.
