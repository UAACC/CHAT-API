# Deployments

One Cloud Run service (`chat-api`) serves every website. Sites are tenants in
`tenants.yaml`; shared, non-secret settings are in `env.yaml`; API keys come
from Secret Manager. Nothing secret is committed.

| File | Contents |
|------|----------|
| `tenants.yaml` | One entry per site: name, origins, model, prompts, knowledge-base namespace, limit overrides |
| `env.yaml` | Shared settings: `TENANTS_FILE`, rate-limit window, context limits, Pinecone index, GCS bucket |
| `ah-studio/knowledge_base.md` | Source document for the A.H. Studio knowledge base (uploaded, not read at runtime) |

Current tenants: `ah-studio` (allisonhe.ca, default, with knowledge base) and
`orctech` (orctech.ca, prompt only).

## Deploy

From the repository root:

```bash
gcloud run deploy chat-api \
  --project=ah-studio-chat --region=us-central1 --source=. --allow-unauthenticated \
  --env-vars-file=deployments/env.yaml \
  --update-secrets="AH_STUDIO_GEMINI_API_KEY=gemini-api-key:latest,ORCTECH_GEMINI_API_KEY=orctech-gemini-api-key:latest,PINECONE_API_KEY=pinecone-api-key:latest,ADMIN_TOKEN=admin-token:latest"
```

`ADMIN_TOKEN` (Secret Manager secret `admin-token`) protects the endpoints
that change a knowledge base. Read it when you need it:
`gcloud secrets versions access latest --secret=admin-token --project=ah-studio-chat`.

`--env-vars-file` replaces the service's plain environment variables, so
`env.yaml` must be complete. Secrets are attached with `--update-secrets` and
survive redeploys. The image contains `deployments/`, which is how
`TENANTS_FILE=deployments/tenants.yaml` resolves inside the container.

The service URL is `https://chat-api-204227115712.us-central1.run.app`; both
websites point their widgets at it.

## Add a site

1. Add an entry to `tenants.yaml`. Required: `origins`, `llm` (with
   `api_key_env`), `prompts.en`. Optional: `prompts.zh`, `knowledge_base`,
   `limits`.
2. Store its LLM key and grant the service account access:

   ```bash
   printf '%s' "$KEY" | gcloud secrets create <site>-gemini-api-key --data-file=- --project=ah-studio-chat
   gcloud secrets add-iam-policy-binding <site>-gemini-api-key --project=ah-studio-chat \
     --member="serviceAccount:204227115712-compute@developer.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

3. Add `<ENV_NAME>=<site>-gemini-api-key:latest` to the `--update-secrets` list
   and deploy. Startup fails fast if the variable named in `api_key_env` is
   empty, so a forgotten secret is caught by the deploy.
4. Point the site's widget at the service URL. Requests are matched to the
   tenant by their `Origin`.

A Gemini key created in a Google Cloud project with no billing account stays
on the free tier; keys under a billed project need prepaid credit.

## Knowledge base

Documents are scoped per site with the `site` query parameter (or inferred
from `Origin`). Writes need the admin token:

```bash
TOKEN=$(gcloud secrets versions access latest --secret=admin-token --project=ah-studio-chat)
API=https://chat-api-204227115712.us-central1.run.app

# Crawl a site (orctech.ca is server-rendered, so this works)
curl -X POST "$API/rag/crawl?site=orctech" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"url": "https://orctech.ca", "max_pages": 30}'

# Upload a document (allisonhe.ca is a client-rendered SPA, so it uses this)
curl -X POST -F "file=@deployments/ah-studio/knowledge_base.md" \
  -H "Authorization: Bearer $TOKEN" "$API/rag/documents/upload?site=ah-studio"

curl "$API/rag/documents?site=ah-studio"
```

A tenant without a `knowledge_base` block answers from its prompt alone; its
document endpoints return `400`. Current knowledge bases: `ah-studio`
(uploaded file, shared namespace) and `orctech` (crawled, namespace `orctech`).

## Local run with the same configuration

```bash
docker build -t chat-api .
docker run --rm -p 8080:8080 -e TENANTS_FILE=deployments/tenants.yaml \
  -e AH_STUDIO_GEMINI_API_KEY=... -e ORCTECH_GEMINI_API_KEY=... chat-api
curl -s -H "Origin: https://orctech.ca" -X POST localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"t","messages":[{"role":"user","content":"What do you do?"}]}'
```
