# AI Chat API - Frontend Connection Guide

This document explains how to connect any frontend application to this AI chat backend.

**Production URL:** `https://chat-api-204227115712.us-central1.run.app`

---

## Quick Start

```javascript
const API_BASE_URL = 'https://chat-api-204227115712.us-central1.run.app';

// Send a chat message with streaming
const response = await fetch(`${API_BASE_URL}/chat/stream`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    session_id: 'unique-session-id',
    messages: [{ role: 'user', content: 'Hello!' }],
    locale: 'en'
  })
});
```

---

## API Endpoints

### 1. Health Check

**GET** `/health`

Check if the API is running.

```bash
curl https://your-api-domain.com/health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "provider": "openai"
}
```

---

### 2. Chat (Streaming) - Recommended

**POST** `/chat/stream`

Send a message and receive the response as a Server-Sent Events (SSE) stream.

**Request Body:**
```json
{
  "session_id": "string",     // Required: Unique session identifier
  "messages": [               // Required: Conversation history
    {
      "role": "user",         // "user" or "assistant"
      "content": "string"     // Message content
    }
  ],
  "locale": "en",             // Optional: "en" | "zh" (default: "en")
  "page_url": "string"        // Optional: Current page URL for context
}
```

**SSE Response Events:**

| Event | Data | Description |
|-------|------|-------------|
| `token` | Text chunk | Partial response token |
| `done` | Empty | Stream completed successfully |
| `error` | Error message | An error occurred |

**Example SSE Stream:**
```
event: token
data: Hello

event: token
data: ! How

event: token
data:  can I help

event: token
data:  you today?

event: done
data:
```

---

### 3. Chat (Non-Streaming)

**POST** `/chat`

Send a message and receive the complete response at once.

**Request Body:** Same as streaming endpoint.

**Response:**
```json
{
  "reply": "Hello! How can I help you today?",
  "session_id": "your-session-id"
}
```

---

## Frontend Implementation Examples

### JavaScript/TypeScript (Vanilla)

```javascript
class ChatClient {
  constructor(baseUrl) {
    this.baseUrl = baseUrl;
    this.sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }

  async sendMessage(messages, locale = 'en', onToken, onDone, onError) {
    const controller = new AbortController();

    try {
      const response = await fetch(`${this.baseUrl}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: this.sessionId,
          messages,
          locale
        }),
        signal: controller.signal
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Request failed');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            const event = line.slice(7);
            // Next line should be data
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6);
            // Handle based on last event type
          }
        }
      }

      onDone?.();
    } catch (error) {
      if (error.name !== 'AbortError') {
        onError?.(error.message);
      }
    }

    return controller; // Return for cancellation
  }

  // Cancel ongoing request
  cancel(controller) {
    controller.abort();
  }
}
```

### React Hook

```javascript
import { useState, useCallback, useRef } from 'react';

export function useChat(apiBaseUrl) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const abortControllerRef = useRef(null);
  const sessionIdRef = useRef(
    `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  );

  const sendMessage = useCallback(async (content, locale = 'en') => {
    const userMessage = { role: 'user', content };
    const updatedMessages = [...messages, userMessage];

    setMessages(updatedMessages);
    setIsLoading(true);
    setError(null);

    abortControllerRef.current = new AbortController();

    try {
      const response = await fetch(`${apiBaseUrl}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          messages: updatedMessages,
          locale
        }),
        signal: abortControllerRef.current.signal
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Request failed');
      }

      // Add empty assistant message
      setMessages(prev => [...prev, { role: 'assistant', content: '' }]);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6);

            if (currentEvent === 'token') {
              setMessages(prev => {
                const newMessages = [...prev];
                const lastMessage = newMessages[newMessages.length - 1];
                lastMessage.content += data;
                return newMessages;
              });
            } else if (currentEvent === 'error') {
              throw new Error(data);
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError(err.message);
        // Remove empty assistant message on error
        setMessages(prev => prev.slice(0, -1));
      }
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
    }
  }, [messages, apiBaseUrl]);

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const clearHistory = useCallback(() => {
    setMessages([]);
    sessionIdRef.current = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }, []);

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    cancel,
    clearHistory
  };
}
```

### Vue 3 Composable

```javascript
import { ref } from 'vue';

export function useChat(apiBaseUrl) {
  const messages = ref([]);
  const isLoading = ref(false);
  const error = ref(null);
  let abortController = null;
  let sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

  async function sendMessage(content, locale = 'en') {
    const userMessage = { role: 'user', content };
    messages.value.push(userMessage);

    isLoading.value = true;
    error.value = null;
    abortController = new AbortController();

    try {
      const response = await fetch(`${apiBaseUrl}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          messages: messages.value,
          locale
        }),
        signal: abortController.signal
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Request failed');
      }

      // Add empty assistant message
      messages.value.push({ role: 'assistant', content: '' });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6);

            if (currentEvent === 'token') {
              const lastMessage = messages.value[messages.value.length - 1];
              lastMessage.content += data;
            } else if (currentEvent === 'error') {
              throw new Error(data);
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        error.value = err.message;
        messages.value.pop(); // Remove empty assistant message
      }
    } finally {
      isLoading.value = false;
      abortController = null;
    }
  }

  function cancel() {
    abortController?.abort();
  }

  function clearHistory() {
    messages.value = [];
    sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    cancel,
    clearHistory
  };
}
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| 200 | Success | Process response |
| 400 | Bad Request | Check request format, message length limits |
| 429 | Rate Limited | Wait and retry (check `Retry-After` header) |
| 500 | Server Error | Retry or show error message |

### Common Errors

**Message too long:**
```json
{
  "detail": "Message too long. Maximum 500 characters allowed."
}
```

**Conversation too long:**
```json
{
  "detail": "Conversation too long. Maximum 20 messages allowed."
}
```

**Rate limited:**
```json
{
  "detail": "Rate limit exceeded. Please wait before sending more messages."
}
```

---

## Best Practices

### 1. Session Management

- Generate a unique `session_id` when the chat starts
- Keep the same `session_id` throughout the conversation
- Generate a new `session_id` when clearing history

```javascript
// Good session ID format
const sessionId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
// Example: "1736789012345-a8b3c9d"
```

### 2. Message History

- Always send the full conversation history (within limits)
- The backend will truncate to the last N messages automatically
- Don't include system messages in the `messages` array

```javascript
// Correct format
const messages = [
  { role: 'user', content: 'Hi there!' },
  { role: 'assistant', content: 'Hello! How can I help?' },
  { role: 'user', content: 'What services do you offer?' }
];
```

### 3. Cancellation

- Implement cancel functionality for better UX
- The backend stops processing when you disconnect

```javascript
const controller = new AbortController();
fetch(url, { signal: controller.signal });

// To cancel:
controller.abort();
```

### 4. Locale Handling

- Use `navigator.language` to detect browser locale
- Map to supported locales: `en` or `zh`

```javascript
function getLocale() {
  const lang = navigator.language.toLowerCase();
  return lang.startsWith('zh') ? 'zh' : 'en';
}
```

### 5. Rate Limit Handling

```javascript
if (response.status === 429) {
  const retryAfter = response.headers.get('Retry-After') || '60';
  showMessage(`Please wait ${retryAfter} seconds before sending another message.`);
}
```

---

## Configuration

### Environment Variables

Set these on your backend deployment:

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | LLM provider (`openai` or `anthropic`) | `openai` |
| `OPENAI_API_KEY` | OpenAI API key | Required if using OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic API key | Required if using Anthropic |
| `CORS_ORIGINS` | Comma-separated allowed origins | `*` |
| `RATE_LIMIT_REQUESTS` | Max requests per window | `20` |
| `RATE_LIMIT_WINDOW` | Rate limit window (seconds) | `60` |
| `MAX_INPUT_LENGTH` | Max characters per message | `500` |
| `MAX_MESSAGES_PER_SESSION` | Max messages in conversation | `20` |
| `MAX_CONTEXT_MESSAGES` | Messages sent to LLM | `10` |

### Custom System Prompts

Option 1: Environment variables
```bash
SYSTEM_PROMPT_EN="You are a helpful assistant for XYZ Company..."
SYSTEM_PROMPT_ZH="您是XYZ公司的AI助手..."
```

Option 2: JSON file
```bash
SYSTEM_PROMPT_FILE=/path/to/prompts.json
```

```json
{
  "en": "You are a helpful assistant for XYZ Company...",
  "zh": "您是XYZ公司的AI助手..."
}
```

---

## CORS Setup

The frontend domain must be in the `CORS_ORIGINS` list:

```bash
CORS_ORIGINS=https://your-frontend.com,https://www.your-frontend.com,http://localhost:3000
```

---

## Testing

### Using cURL

```bash
# Health check
curl https://your-api-domain.com/health

# Non-streaming chat
curl -X POST https://your-api-domain.com/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-123",
    "messages": [{"role": "user", "content": "Hello!"}],
    "locale": "en"
  }'

# Streaming chat
curl -X POST https://your-api-domain.com/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-123",
    "messages": [{"role": "user", "content": "Hello!"}],
    "locale": "en"
  }'
```

### Using JavaScript Console

```javascript
// Quick test in browser console
fetch('https://your-api-domain.com/health')
  .then(r => r.json())
  .then(console.log);
```

---

## Troubleshooting

### CORS Errors

**Error:** `Access to fetch has been blocked by CORS policy`

**Solution:** Ensure your frontend domain is in `CORS_ORIGINS`

### Connection Refused

**Error:** `net::ERR_CONNECTION_REFUSED`

**Solution:** Check if the API server is running and accessible

### SSE Not Working

**Error:** SSE events not being received

**Solutions:**
1. Check that the response `Content-Type` is `text/event-stream`
2. Ensure no proxy is buffering the response
3. Check for intermediate caching (use `Cache-Control: no-cache`)

### Rate Limiting

**Error:** Frequent 429 responses

**Solution:** Implement exponential backoff or show user-friendly message

---

## Support

For issues or questions, check the server logs or contact the backend maintainer.
