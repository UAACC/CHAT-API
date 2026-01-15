# CHAT-API - Code Usage Guide

A detailed guide for developers to understand, modify, and deploy the AI chat backend.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [File Structure](#file-structure)
3. [How to Switch LLM Models](#how-to-switch-llm-models)
4. [How to Edit Memory/Context Settings](#how-to-edit-memorycontext-settings)
5. [How to Customize System Prompts](#how-to-customize-system-prompts)
6. [How Messages Flow (Frontend ↔ Backend)](#how-messages-flow-frontend--backend)
7. [How to Add Rate Limiting Rules](#how-to-add-rate-limiting-rules)
8. [Local Development](#local-development)
9. [Deployment Commands](#deployment-commands)
10. [Troubleshooting](#troubleshooting)

---

## Project Overview

This is a FastAPI backend that:
1. Receives chat messages from a frontend
2. Sends them to an LLM (OpenAI/Anthropic) via LangChain
3. Streams the response back as Server-Sent Events (SSE)

**Tech Stack:**
- **FastAPI** - Web framework
- **LangChain** - LLM integration
- **SSE-Starlette** - Server-Sent Events for streaming
- **Pydantic** - Data validation
- **Docker** - Containerization
- **Google Cloud Run** - Hosting

---

## File Structure

```
CHAT-API/
├── app/
│   ├── __init__.py
│   ├── main.py              # App entry point, CORS setup
│   ├── config.py            # ★ All settings (models, limits, etc.)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── health.py        # Health check endpoint
│   │   └── chat.py          # ★ Chat endpoints (main logic)
│   ├── services/
│   │   ├── __init__.py
│   │   └── llm_service.py   # ★ LLM integration (model switching)
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py       # Request/response models
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── rate_limit.py    # Rate limiting logic
│   └── prompts/
│       ├── __init__.py
│       └── system_prompts.py # ★ AI personality/behavior
├── Dockerfile               # Container build
├── requirements.txt         # Python dependencies
├── .env                     # Local environment variables
├── .env.example             # Template
└── .gcloudignore            # Files to exclude from deploy
```

**★ = Files you'll most likely need to modify**

---

## How to Switch LLM Models

### Option 1: Change Model via Environment Variable (Recommended)

**For Local Development:**

Edit `.env` file:
```env
# OpenAI Models
OPENAI_MODEL=gpt-4o-mini      # Fast, cheap (default)
OPENAI_MODEL=gpt-4o           # Smarter, more expensive
OPENAI_MODEL=gpt-4-turbo      # Latest GPT-4

# Or switch to Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-3-haiku-20240307    # Fast, cheap
ANTHROPIC_MODEL=claude-3-sonnet-20240229   # Balanced
ANTHROPIC_MODEL=claude-3-opus-20240229     # Most capable
```

Then restart the server.

**For Production (Cloud Run):**

```powershell
gcloud run services update chat-api \
    --region us-central1 \
    --set-env-vars "OPENAI_MODEL=gpt-4o"
```

### Option 2: Change Model in Code

Edit `app/config.py`:

```python
# Line 23-24
openai_model: str = "gpt-4o-mini"      # Change this
anthropic_model: str = "claude-3-haiku-20240307"  # Or this
```

### Option 3: Switch Between OpenAI and Anthropic

**In `.env`:**
```env
# Use OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-proj-xxx

# OR Use Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
```

**How it works in code (`app/services/llm_service.py`):**

```python
def get_llm() -> BaseChatModel:
    settings = get_settings()

    if settings.llm_provider == "openai":
        # Uses OpenAI
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.openai_model,      # ← Model name from config
            api_key=settings.openai_api_key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            streaming=True,
        )

    elif settings.llm_provider == "anthropic":
        # Uses Anthropic
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.anthropic_model,   # ← Model name from config
            api_key=settings.anthropic_api_key,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            streaming=True,
        )
```

### Adding a New LLM Provider

1. Install the LangChain integration:
   ```bash
   pip install langchain-google-genai  # Example: Google Gemini
   ```

2. Add to `app/config.py`:
   ```python
   llm_provider: Literal["openai", "anthropic", "google"] = "openai"
   google_api_key: str = ""
   google_model: str = "gemini-pro"
   ```

3. Add to `app/services/llm_service.py`:
   ```python
   elif settings.llm_provider == "google":
       from langchain_google_genai import ChatGoogleGenerativeAI
       return ChatGoogleGenerativeAI(
           model=settings.google_model,
           google_api_key=settings.google_api_key,
           streaming=True,
       )
   ```

---

## How to Edit Memory/Context Settings

Memory settings control how much conversation history is sent to the LLM.

### Settings Location: `app/config.py`

```python
# Line 40-43
# Cost Protection
max_input_length: int = 500        # Max characters per user message
max_messages_per_session: int = 20 # Max messages in conversation
max_context_messages: int = 10     # ★ Only send last N messages to LLM
```

### What Each Setting Does

| Setting | Default | Purpose |
|---------|---------|---------|
| `max_input_length` | 500 | Rejects messages longer than this |
| `max_messages_per_session` | 20 | Rejects conversations with more messages |
| `max_context_messages` | 10 | **Only sends last 10 messages to LLM** |

### How Context Truncation Works

**In `app/routes/chat.py`:**

```python
def truncate_messages(messages: list) -> list:
    """
    Truncate conversation to only include recent messages.
    Keeps the last N messages to reduce token usage.
    """
    if len(messages) <= settings.max_context_messages:
        return messages  # Return all if under limit

    # Only keep the last N messages
    return messages[-settings.max_context_messages:]
```

**Example:**
```
User sends 15 messages:
[msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8, msg9, msg10, msg11, msg12, msg13, msg14, msg15]

With max_context_messages=10, LLM receives:
[msg6, msg7, msg8, msg9, msg10, msg11, msg12, msg13, msg14, msg15]
```

### Changing Memory Settings

**Option 1: Environment Variable (Recommended)**

In `.env`:
```env
MAX_CONTEXT_MESSAGES=20  # Send more history to LLM
```

**Option 2: In Code**

Edit `app/config.py`:
```python
max_context_messages: int = 20  # Change from 10 to 20
```

### Trade-offs

| More Context | Less Context |
|--------------|--------------|
| Better conversation continuity | Faster responses |
| Higher token cost | Lower token cost |
| May hit token limits | Always under limits |

**Recommendation:** Start with 10, increase if users complain about AI "forgetting" things.

---

## How to Customize System Prompts

The system prompt defines the AI's personality, knowledge, and behavior.

### Location: `app/prompts/system_prompts.py`

### Option 1: Edit Default Prompts in Code

```python
# Line 19-49
DEFAULT_PROMPT_EN = """You are a helpful AI assistant.

## Your Role
- Answer questions accurately and helpfully
- Be conversational but professional
- Only provide information you're confident about

## Guidelines
- Keep responses concise and relevant
- If unsure, say so honestly
- Never make up information
"""

DEFAULT_PROMPT_ZH = """您是一位乐于助人的AI助手。
...
"""
```

### Option 2: Set via Environment Variables

In `.env`:
```env
SYSTEM_PROMPT_EN=You are a helpful assistant for MyCompany. You help customers with product questions and support issues.
SYSTEM_PROMPT_ZH=您是MyCompany的AI助手。您帮助客户解答产品问题和支持问题。
```

### Option 3: Load from JSON File

Create `prompts.json`:
```json
{
  "en": "You are a helpful assistant for MyCompany...",
  "zh": "您是MyCompany的AI助手..."
}
```

In `.env`:
```env
SYSTEM_PROMPT_FILE=/path/to/prompts.json
```

### How Prompts are Loaded

**In `app/prompts/system_prompts.py`:**

```python
@lru_cache
def load_prompts() -> dict:
    """
    Priority:
    1. SYSTEM_PROMPT_FILE (JSON file)
    2. SYSTEM_PROMPT_EN / SYSTEM_PROMPT_ZH env vars
    3. Default prompts in code
    """
    settings = get_settings()
    prompts = {}

    # Try loading from file first
    if settings.system_prompt_file:
        # Load from JSON file
        ...

    # Try environment variables
    if settings.system_prompt_en:
        prompts['en'] = settings.system_prompt_en

    if settings.system_prompt_zh:
        prompts['zh'] = settings.system_prompt_zh

    # Fall back to defaults
    if 'en' not in prompts:
        prompts['en'] = DEFAULT_PROMPT_EN
    if 'zh' not in prompts:
        prompts['zh'] = DEFAULT_PROMPT_ZH

    return prompts


def get_system_prompt(locale: str) -> str:
    """Get prompt for specific language."""
    prompts = load_prompts()
    lang = 'zh' if locale.startswith('zh') else 'en'
    return prompts.get(lang, prompts['en'])
```

### Writing Good System Prompts

**Template:**
```
You are [ROLE] for [COMPANY/PURPOSE].

## About [COMPANY]
- [Key fact 1]
- [Key fact 2]

## Your Capabilities
- [What you can help with]
- [What you can help with]

## Guidelines
- [Behavior rule 1]
- [Behavior rule 2]

## Limitations
- [What you cannot do]
- [When to redirect to humans]
```

---

## How Messages Flow (Frontend ↔ Backend)

### Complete Flow Diagram

```
┌─────────────┐     POST /chat/stream      ┌─────────────┐
│             │ ─────────────────────────▶ │             │
│  Frontend   │   {session_id, messages,   │   Backend   │
│  (React)    │    locale}                 │  (FastAPI)  │
│             │ ◀───────────────────────── │             │
└─────────────┘     SSE: token, token,     └─────────────┘
                    token, done                   │
                                                  │
                                                  ▼
                                          ┌─────────────┐
                                          │   OpenAI    │
                                          │     API     │
                                          └─────────────┘
```

### Step-by-Step Flow

**Step 1: Frontend sends request**

```javascript
// Frontend: src/lib/aiClient.js
fetch(`${API_BASE_URL}/chat/stream`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    session_id: 'user-123-abc',
    messages: [
      { role: 'user', content: 'Hello!' },
      { role: 'assistant', content: 'Hi! How can I help?' },
      { role: 'user', content: 'What services do you offer?' }
    ],
    locale: 'en'
  })
})
```

**Step 2: Backend receives and validates**

```python
# Backend: app/routes/chat.py

@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    # 1. Check rate limit
    check_rate_limit(request)  # Throws 429 if exceeded

    # 2. Validate request
    validate_request(body)  # Throws 400 if invalid
    # - Checks message count <= 20
    # - Checks last message length <= 500 chars

    # 3. Truncate to last N messages
    truncated_messages = truncate_messages(body.messages)
```

**Step 3: Backend builds LangChain messages**

```python
# Backend: app/services/llm_service.py

def build_langchain_messages(messages: list[ChatMessage], locale: str) -> list:
    # Start with system prompt
    langchain_messages = [
        SystemMessage(content=get_system_prompt(locale))
    ]

    # Add conversation history
    for msg in messages:
        if msg.role == "user":
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            langchain_messages.append(AIMessage(content=msg.content))

    return langchain_messages

# Result:
# [
#   SystemMessage("You are a helpful assistant..."),
#   HumanMessage("Hello!"),
#   AIMessage("Hi! How can I help?"),
#   HumanMessage("What services do you offer?")
# ]
```

**Step 4: Backend calls LLM and streams response**

```python
# Backend: app/routes/chat.py

async def event_generator():
    try:
        # Stream tokens from LLM
        async for token in generate_response_stream(truncated_messages, body.locale):
            # Check if client disconnected (saves tokens!)
            if await request.is_disconnected():
                break

            # Send token as SSE event
            yield {
                "event": "token",
                "data": token,  # e.g., "We", " offer", " art", " classes"
            }

        # Send completion event
        yield {
            "event": "done",
            "data": "",
        }

    except Exception as e:
        yield {
            "event": "error",
            "data": str(e),
        }

return EventSourceResponse(event_generator())
```

**Step 5: Frontend receives SSE stream**

```javascript
// Frontend: src/lib/aiClient.js

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const chunk = decoder.decode(value);
  // chunk = "event: token\ndata: We\n\nevent: token\ndata:  offer\n\n"

  const lines = chunk.split('\n');
  for (const line of lines) {
    if (line.startsWith('event: token')) {
      // Next line has the token
    } else if (line.startsWith('data: ')) {
      const token = line.slice(6);
      onToken(token);  // Update UI with this token
    } else if (line.startsWith('event: done')) {
      onDone();  // Stream complete
    }
  }
}
```

### Request/Response Schemas

**Request (`app/models/schemas.py`):**

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

class ChatRequest(BaseModel):
    session_id: str                    # Unique session identifier
    messages: list[ChatMessage]        # Conversation history
    locale: str = "en"                 # Language: "en" or "zh"
    page_url: Optional[str] = None     # Current page (optional context)
```

**Response (SSE events):**

```
event: token
data: Hello

event: token
data: ! How

event: token
data:  can I help?

event: done
data:
```

**Non-streaming response (`/chat` endpoint):**

```python
class ChatResponse(BaseModel):
    reply: str           # Complete response
    session_id: str      # Echo back session ID
```

---

## How to Add Rate Limiting Rules

### Current Rate Limiting

**Location:** `app/middleware/rate_limit.py`

**Settings in `app/config.py`:**
```python
rate_limit_requests: int = 20   # Max requests
rate_limit_window: int = 60     # Per 60 seconds
```

### How It Works

```python
# app/middleware/rate_limit.py

class RateLimiter:
    def __init__(self):
        self.requests = {}  # {ip: [(timestamp, count), ...]}

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.time()
        window_start = now - window_seconds

        # Get requests in current window
        if key not in self.requests:
            self.requests[key] = []

        # Filter to only requests in window
        self.requests[key] = [
            (ts, count) for ts, count in self.requests[key]
            if ts > window_start
        ]

        # Count total requests
        total = sum(count for _, count in self.requests[key])

        if total >= max_requests:
            return False  # Rate limited!

        # Record this request
        self.requests[key].append((now, 1))
        return True
```

### Changing Rate Limits

**Option 1: Environment Variables**

```env
RATE_LIMIT_REQUESTS=50   # Allow 50 requests
RATE_LIMIT_WINDOW=120    # Per 2 minutes
```

**Option 2: In Code**

Edit `app/config.py`:
```python
rate_limit_requests: int = 50
rate_limit_window: int = 120
```

### Adding Custom Rate Limit Rules

**Example: Different limits for different endpoints**

Edit `app/routes/chat.py`:

```python
from app.middleware.rate_limit import limiter, check_rate_limit
from app.config import get_settings

@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    settings = get_settings()

    # Custom rate limit for streaming (stricter)
    client_ip = request.client.host
    if not limiter.is_allowed(
        key=f"stream:{client_ip}",
        max_requests=10,        # Only 10 streaming requests
        window_seconds=60       # Per minute
    ):
        raise HTTPException(status_code=429, detail="Rate limit exceeded for streaming")

    # ... rest of the code
```

**Example: Rate limit by session**

```python
@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    # Rate limit by session ID (prevents session abuse)
    if not limiter.is_allowed(
        key=f"session:{body.session_id}",
        max_requests=30,
        window_seconds=300  # 30 messages per 5 minutes per session
    ):
        raise HTTPException(status_code=429, detail="Too many messages in this session")
```

---

## Local Development

### Prerequisites

- Python 3.11+
- pip
- OpenAI API key (or Anthropic)

### Setup Steps

```powershell
# 1. Navigate to project
cd W:\CHAT-API

# 2. Create virtual environment
python -m venv venv

# 3. Activate virtual environment
.\venv\Scripts\Activate.ps1    # Windows PowerShell
# OR
source venv/bin/activate        # Linux/Mac

# 4. Install dependencies
pip install -r requirements.txt

# 5. Create environment file
copy .env.example .env

# 6. Edit .env with your API key
notepad .env
# Set: OPENAI_API_KEY=sk-proj-your-key-here

# 7. Run the server
uvicorn app.main:app --reload --port 8080
```

### Verify It's Working

```powershell
# Health check
curl http://localhost:8080/health

# Test chat
curl -X POST http://localhost:8080/chat `
  -H "Content-Type: application/json" `
  -d '{"session_id":"test","messages":[{"role":"user","content":"Hi"}],"locale":"en"}'
```

### Common Development Tasks

**Change model and restart:**
```powershell
# Edit .env
OPENAI_MODEL=gpt-4o

# Server auto-reloads with --reload flag
```

**View logs:**
```
Server shows all requests and errors in terminal
```

**Test streaming:**
```powershell
curl -X POST http://localhost:8080/chat/stream `
  -H "Content-Type: application/json" `
  -d '{"session_id":"test","messages":[{"role":"user","content":"Tell me a story"}],"locale":"en"}'
```

---

## Deployment Commands

### Prerequisites

```powershell
# Install Google Cloud CLI
# https://cloud.google.com/sdk/docs/install

# Login
gcloud auth login

# Set project
gcloud config set project ah-studio-chat
```

### First-Time Deployment

```powershell
# 1. Enable required APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com

# 2. Create API key secret
gcloud secrets create openai-api-key --replication-policy="automatic"

# 3. Add key to secret (no newline!)
$key = "sk-proj-your-key-here"
[IO.File]::WriteAllText("$env:TEMP\apikey.txt", $key)
gcloud secrets versions add openai-api-key --data-file="$env:TEMP\apikey.txt"
Remove-Item "$env:TEMP\apikey.txt"

# 4. Grant Cloud Run access to secret
gcloud secrets add-iam-policy-binding openai-api-key `
    --member="serviceAccount:204227115712-compute@developer.gserviceaccount.com" `
    --role="roles/secretmanager.secretAccessor"

# 5. Build container
cd W:\CHAT-API
gcloud builds submit --tag gcr.io/ah-studio-chat/chat-api

# 6. Deploy
gcloud run deploy chat-api `
    --image gcr.io/ah-studio-chat/chat-api `
    --platform managed `
    --region us-central1 `
    --allow-unauthenticated `
    --set-env-vars "LLM_PROVIDER=openai" `
    --set-env-vars "OPENAI_MODEL=gpt-4o-mini" `
    --set-env-vars "CORS_ORIGINS=https://allisonhe.ca;https://www.allisonhe.ca;http://localhost:5173" `
    --set-secrets "OPENAI_API_KEY=openai-api-key:latest" `
    --memory 512Mi `
    --min-instances 0 `
    --max-instances 10
```

### Update Deployment (Code Changes)

```powershell
# 1. Build new container
cd W:\CHAT-API
gcloud builds submit --tag gcr.io/ah-studio-chat/chat-api

# 2. Deploy new version
gcloud run deploy chat-api `
    --image gcr.io/ah-studio-chat/chat-api `
    --region us-central1
```

### Update Environment Variables Only

```powershell
# Change model
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "OPENAI_MODEL=gpt-4o"

# Change rate limits
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "RATE_LIMIT_REQUESTS=50"

# Change CORS (add new domain)
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "CORS_ORIGINS=https://allisonhe.ca;https://www.allisonhe.ca;https://new-domain.com"
```

### Update API Key

```powershell
# Add new version to secret
$key = "sk-proj-new-key-here"
[IO.File]::WriteAllText("$env:TEMP\apikey.txt", $key)
gcloud secrets versions add openai-api-key --data-file="$env:TEMP\apikey.txt"
Remove-Item "$env:TEMP\apikey.txt"

# Redeploy to pick up new secret
gcloud run deploy chat-api `
    --image gcr.io/ah-studio-chat/chat-api `
    --region us-central1 `
    --set-secrets "OPENAI_API_KEY=openai-api-key:latest"
```

### Toggle Cold Start

```powershell
# Cost saving (5-15s cold start, free when idle)
gcloud run services update chat-api --region us-central1 --min-instances 0

# Always warm (~$15-30/month, always fast)
gcloud run services update chat-api --region us-central1 --min-instances 1
```

### View Logs

```powershell
# Recent logs
gcloud run services logs read chat-api --region us-central1 --limit 50

# Stream logs (live)
gcloud beta run services logs tail chat-api --region us-central1
```

### Rollback to Previous Version

```powershell
# List revisions
gcloud run revisions list --service chat-api --region us-central1

# Route traffic to previous version
gcloud run services update-traffic chat-api `
    --region us-central1 `
    --to-revisions chat-api-00003-abc=100
```

---

## Troubleshooting

### Error: "Connection error"

**Cause:** Usually API key issue

**Check:**
```powershell
# View logs for details
gcloud run services logs read chat-api --region us-central1 --limit 20

# Verify secret value
gcloud secrets versions access latest --secret=openai-api-key
```

**Fix:** Recreate secret without trailing newline (see deployment commands)

---

### Error: "CORS error" in browser

**Cause:** Frontend domain not in CORS_ORIGINS

**Fix:**
```powershell
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "CORS_ORIGINS=https://your-frontend.com;http://localhost:5173"
```

---

### Error: "Rate limit exceeded"

**Cause:** Too many requests from same IP

**Fix:** Increase limits or wait
```powershell
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "RATE_LIMIT_REQUESTS=50"
```

---

### Error: "Message too long"

**Cause:** User message exceeds MAX_INPUT_LENGTH

**Fix:** Increase limit
```powershell
gcloud run services update chat-api `
    --region us-central1 `
    --set-env-vars "MAX_INPUT_LENGTH=1000"
```

---

### Slow First Request (5-15 seconds)

**Cause:** Cold start (--min-instances 0)

**Fix:** Keep one instance warm
```powershell
gcloud run services update chat-api --region us-central1 --min-instances 1
```

---

### Container Won't Start

**Cause:** Usually Dockerfile issue

**Check:**
```powershell
# View build logs
gcloud builds list --limit 5

# View specific build
gcloud builds log BUILD_ID
```

**Common fixes:**
- Ensure CMD listens on `$PORT`
- Ensure all dependencies in requirements.txt
- Check for syntax errors in Python files

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│                    CHAT-API Quick Reference                 │
├─────────────────────────────────────────────────────────────┤
│ LOCAL DEV                                                   │
│   cd W:\CHAT-API                                            │
│   .\venv\Scripts\Activate.ps1                               │
│   uvicorn app.main:app --reload --port 8080                 │
├─────────────────────────────────────────────────────────────┤
│ DEPLOY CODE CHANGES                                         │
│   gcloud builds submit --tag gcr.io/ah-studio-chat/chat-api │
│   gcloud run deploy chat-api --image gcr.io/ah-studio-chat/chat-api --region us-central1 │
├─────────────────────────────────────────────────────────────┤
│ UPDATE ENV VARS                                             │
│   gcloud run services update chat-api --region us-central1  │
│     --set-env-vars "VAR_NAME=value"                         │
├─────────────────────────────────────────────────────────────┤
│ VIEW LOGS                                                   │
│   gcloud run services logs read chat-api --region us-central1 --limit 30 │
├─────────────────────────────────────────────────────────────┤
│ TOGGLE COLD START                                           │
│   --min-instances 0  (free, slow first request)             │
│   --min-instances 1  (~$15-30/mo, always fast)              │
├─────────────────────────────────────────────────────────────┤
│ KEY FILES TO EDIT                                           │
│   app/config.py           - Settings (models, limits)       │
│   app/prompts/system_prompts.py - AI personality            │
│   app/routes/chat.py      - Chat logic                      │
│   app/services/llm_service.py - LLM switching               │
└─────────────────────────────────────────────────────────────┘
```

---

*Last updated: January 2026*
