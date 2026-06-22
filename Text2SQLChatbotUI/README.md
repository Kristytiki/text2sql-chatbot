# Text2SQLChatbotUI

React + Vite frontend for the Census Text2SQL agent. Single-pane chat with a
collapsible "View SQL" panel showing the grounding query.

## Dev

```bash
npm install
npm run dev   # → http://localhost:5173, proxies /chat → :8000
```

The backend (`Text2SQLChatbotService`) must be running on port 8000.

## Access key

The UI is gated by a shared access key. On first load it prompts for a key,
stores it in `localStorage`, and sends it as the `X-API-Key` header on every
request. The backend rejects requests without a matching `API_KEY` (set in the
service `.env`). A rejected key (HTTP 401) drops the user back to the gate.

If `API_KEY` is empty in the backend `.env`, auth is disabled (local dev only)
and the gate still appears but any non-empty value is accepted by the server.

## Public sharing (Cloudflare tunnel)

```bash
./scripts/share.sh
```

This starts the backend, the UI, and a Cloudflare tunnel, then prints a public
`https://<random>.trycloudflare.com` URL. The tunnel points at Vite, which
proxies `/chat` and `/health` to the backend — one origin, no CORS. Share the
URL **and** the access key with whoever needs in; the URL alone is useless
without the key.

Requires [`cloudflared`](https://github.com/cloudflare/cloudflared) on PATH and
a non-empty `API_KEY` in the backend `.env`.
