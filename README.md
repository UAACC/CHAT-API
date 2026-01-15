# CHAT-API

Reusable AI-powered chat assistant backend with SSE streaming support.

## Features

- **Multi-provider LLM support**: OpenAI, Anthropic (easily extensible)
- **SSE streaming**: Real-time token-by-token responses
- **Bilingual**: English and Chinese system prompts
- **Cost protection**: Rate limiting, message limits, context truncation
- **Configurable**: All settings via environment variables
- **Cloud-ready**: Dockerfile for easy deployment

## Tech Stack

- **Framework**: FastAPI
- **LLM Integration**: LangChain
- **LLM Providers**: OpenAI (default), Anthropic
- **Streaming**: Server-Sent Events (SSE)
- **Deployment**: Docker / Google Cloud Run

## Quick Start

### 1. Setup Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run Locally

```bash
# Development mode with auto-reload
uvicorn app.main:app --reload --port 8080

# Or using Python directly
python -m app.main
```

### 3. Test Endpoints

```bash
# Health check
curl http://localhost:8080/health

# Non-streaming chat
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-123",
    "messages": [{"role": "user", "content": "Hello!"}],
    "locale": "en"
  }'

# Streaming chat (SSE)
curl -X POST http://localhost:8080/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-123",
    "messages": [{"role": "user", "content": "Tell me about yourself"}],
    "locale": "en"
  }'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check for Cloud Run |
| `/chat` | POST | Non-streaming chat response |
| `/chat/stream` | POST | SSE streaming chat response |
| `/docs` | GET | OpenAPI documentation |

See [CONNECTIONBOOK.md](./CONNECTIONBOOK.md) for detailed frontend integration guide.

## Deployment to Cloud Run

### Prerequisites

- Google Cloud SDK installed
- Project created with billing enabled

### Deploy

```bash
# Set project
gcloud config set project YOUR_PROJECT_ID

# Enable APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

# Create secret for API key
echo -n "sk-your-openai-key" | gcloud secrets create openai-api-key --data-file=-

# Build and deploy
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/chat-api

gcloud run deploy chat-api \
    --image gcr.io/YOUR_PROJECT_ID/chat-api \
    --platform managed \
    --region us-central1 \
    --allow-unauthenticated \
    --set-env-vars "LLM_PROVIDER=openai,OPENAI_MODEL=gpt-4o-mini,CORS_ORIGINS=https://your-domain.com" \
    --set-secrets "OPENAI_API_KEY=openai-api-key:latest" \
    --memory 512Mi \
    --min-instances 0 \
    --max-instances 10

# Map custom domain (optional)
gcloud run domain-mappings create \
    --service chat-api \
    --domain api.your-domain.com \
    --region us-central1
```

## Configuration

See `.env.example` for all configuration options.

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `CHAT-API` | Application name |
| `LLM_PROVIDER` | `openai` | LLM provider (openai, anthropic) |
| `OPENAI_API_KEY` | - | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model name |
| `MAX_TOKENS` | `512` | Max response tokens |
| `TEMPERATURE` | `0.7` | Response creativity |
| `RATE_LIMIT_REQUESTS` | `20` | Requests per window |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window (seconds) |
| `MAX_INPUT_LENGTH` | `500` | Max characters per message |
| `MAX_MESSAGES_PER_SESSION` | `20` | Max messages in conversation |
| `MAX_CONTEXT_MESSAGES` | `10` | Messages sent to LLM |
| `CORS_ORIGINS` | `localhost` | Allowed CORS origins |
| `SYSTEM_PROMPT_EN` | - | Custom English system prompt |
| `SYSTEM_PROMPT_ZH` | - | Custom Chinese system prompt |
| `SYSTEM_PROMPT_FILE` | - | Path to prompts JSON file |

## Custom System Prompts

### Option 1: Environment Variables

```bash
SYSTEM_PROMPT_EN="You are a helpful assistant for MyCompany..."
SYSTEM_PROMPT_ZH="您是MyCompany的AI助手..."
```

### Option 2: JSON File

```bash
SYSTEM_PROMPT_FILE=/path/to/prompts.json
```

```json
{
  "en": "You are a helpful assistant for MyCompany...",
  "zh": "您是MyCompany的AI助手..."
}
```

## Project Structure

```
chat-api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app
│   ├── config.py            # Environment config
│   ├── routes/
│   │   ├── health.py        # Health endpoint
│   │   └── chat.py          # Chat endpoints
│   ├── services/
│   │   └── llm_service.py   # LangChain integration
│   ├── models/
│   │   └── schemas.py       # Pydantic models
│   ├── middleware/
│   │   └── rate_limit.py    # Rate limiting
│   └── prompts/
│       └── system_prompts.py # System prompts
├── Dockerfile
├── requirements.txt
├── .env.example
├── CONNECTIONBOOK.md        # Frontend integration guide
└── README.md
```

## License

MIT
