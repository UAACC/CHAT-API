# Embeddable widget

Status: approved 2026-09-05

## Goal

Any website adds one `<script>` tag and gets a complete chat assistant that
talks to this API. No build step for the site, no configuration beyond
optional `data-*` attributes; the page's `Origin` selects the tenant.

## Usage

```html
<script src="https://<service>/widget.js"
        data-title="Studio Assistant"
        data-greeting="Hello! How can we help?"
        data-accent="#a8c686"
        data-theme="dark"
        data-position="right"
        data-locale="en"
        data-site="studio"></script>
```

| Attribute | Default | Meaning |
|-----------|---------|---------|
| `data-title` | `Assistant` | Panel header |
| `data-greeting` | `Hello! How can I help you today?` | Empty-state text |
| `data-accent` | `#2563eb` | Launcher and send-button colour |
| `data-theme` | `auto` | `dark`, `light` or `auto` (follows `prefers-color-scheme`) |
| `data-position` | `right` | `right` or `left` |
| `data-locale` | `en` | Initial language, `en` or `zh`; a toggle in the header switches |
| `data-site` | – | Tenant id; only needed when the page's origin is not a tenant origin (demo page, local files) |
| `data-api` | script origin | Override the API base URL |

`window.ChatWidget` exposes `open()`, `close()`, `toggle()` and `send(text)`.

## Delivery

- `app/widget/widget.js`: one hand-written file, no dependencies, styles
  injected into a Shadow DOM so the host page's CSS and the widget's never
  interact. Target under 10 KB gzipped.
- `GET /widget.js`: served by FastAPI with `Cache-Control: public, max-age=3600`,
  a content-hash `ETag`, and `304` on `If-None-Match`.
- `GET /widget/demo`: an HTML page that embeds the widget from the same
  service, with a `?site=` parameter to pick the tenant. Doubles as the
  manual test page and as a link in the README.

## Behaviour

- Launcher button fixed at the bottom corner; panel 380×560 on desktop, full
  width on phones; `Esc` closes; focus moves to the input on open.
- Header: title, EN/中文 toggle, new-conversation, close.
- Messages render as plain text with line breaks preserved; `**bold**` and
  bare `https://` links are the only formatting recognised (escaped first).
- Streaming via the SSE protocol; a Stop button aborts the request; errors
  show an inline notice with a retry; the pending assistant bubble shows a
  typing indicator.
- Conversation and language persist in `localStorage` under
  `chat-widget:<site or page origin>`; new-conversation clears them and
  rotates the session id.
- Requests: `POST <api>/chat/stream` with `session_id`, `messages`, `locale`,
  `page_url`, and `site` when configured.

## Tenant resolution change

A page served by the API itself (the demo) sends an `Origin` equal to the
service's own host. `TenantRegistry.resolve` treats a same-host origin like
no origin: explicit `site` wins, otherwise the default tenant. Cross-site
origins behave as before.

## Testing

- pytest: `/widget.js` content type, cache headers, `ETag` and `304`;
  `/widget/demo` is HTML and references `/widget.js`; same-host origin
  resolves to the default tenant, and to the explicit site when given.
- Manual, before each deploy: Playwright on the demo page with a real model:
  open, send, stream, stop, language toggle, persistence after reload.

## Out of scope

Replacing the React widgets on the two existing sites; a build pipeline;
file uploads; rich markdown.
