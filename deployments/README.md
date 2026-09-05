# Deployments

One backend, one Cloud Run service per website. Everything that makes a
deployment site-specific (prompts, CORS origins, model, knowledge-base
settings) lives in `<site>/env.yaml`; API keys are attached from Secret
Manager and never committed.

| Site | Service | Config | Knowledge base |
|------|---------|--------|----------------|
| allisonhe.ca | `chat-api` | `ah-studio/env.yaml` | Pinecone index `chat-api-rag-ahstudio`, source in `ah-studio/knowledge_base.md` |
| orctech.ca | `orctech-chat-api` | `orctech/env.yaml` | none (prompt only) |

## Deploy or update a site

```bash
# A.H. Studio
gcloud run deploy chat-api \
  --project=ah-studio-chat --region=us-central1 --source=. \
  --env-vars-file=deployments/ah-studio/env.yaml \
  --update-secrets="GEMINI_API_KEY=gemini-api-key:latest,PINECONE_API_KEY=pinecone-api-key:latest"

# OrcTech
gcloud run deploy orctech-chat-api \
  --project=ah-studio-chat --region=us-central1 --source=. --allow-unauthenticated \
  --env-vars-file=deployments/orctech/env.yaml \
  --update-secrets="GEMINI_API_KEY=orctech-gemini-api-key:latest"
```

`--env-vars-file` replaces the service's plain environment variables with the
file's contents, so the file must be complete. Secrets are passed with
`--update-secrets` so they survive redeploys.

## Add a new site

1. Copy `orctech/` to `<site>/` and edit `env.yaml`: prompts, `CORS_ORIGINS`,
   `APP_NAME`. Leave `PINECONE_INDEX` unset unless the site has a knowledge base.
2. Store the site's LLM key: `gcloud secrets create <site>-gemini-api-key --data-file=key.txt`
   and grant `roles/secretmanager.secretAccessor` to the Cloud Run service account.
3. Deploy with the command pattern above, using a new service name.
4. Point the website's chat widget at the service URL.

## Upload a knowledge base document

```bash
curl -X POST -F "file=@deployments/ah-studio/knowledge_base.md" \
  https://<service-url>/rag/documents/upload
```
