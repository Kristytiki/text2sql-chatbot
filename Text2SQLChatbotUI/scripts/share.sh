#!/usr/bin/env bash
#
# Expose the Census chatbot to the public internet via a Cloudflare tunnel.
#
#   tunnel ──> Vite (:5173) ──proxy──> FastAPI backend (:8000)
#
# One public https URL. The UI's access-key gate + the backend's X-API-Key
# check guard every /chat request, so the public URL is useless without the key.
#
# Prereqs:
#   - cloudflared on PATH            (https://github.com/cloudflare/cloudflared)
#   - API_KEY set in the backend .env (see Text2SQLChatbotService/.env.example)
#   - backend deps + UI deps installed
#
# Usage:  ./scripts/share.sh
# Stop:   Ctrl-C  (tears down all three processes)
set -euo pipefail

UI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVC_DIR="$(cd "$UI_DIR/../Text2SQLChatbotService" && pwd)"

command -v cloudflared >/dev/null || { echo "✗ cloudflared not found on PATH"; exit 1; }

# Warn loudly if the backend has no key configured — public + no auth is a footgun.
if [[ -f "$SVC_DIR/.env" ]] && ! grep -qE '^API_KEY=.+' "$SVC_DIR/.env"; then
  echo "⚠️  API_KEY is empty in $SVC_DIR/.env — the public URL would be UNAUTHENTICATED."
  echo "    Generate one:  python -c 'import secrets; print(secrets.token_urlsafe(32))'"
  read -r -p "    Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1
fi

pids=()
cleanup() { echo; echo "→ shutting down…"; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "→ starting backend (:8000)…"
( cd "$SVC_DIR" && python -m text2sqlchatbotservice.app ) &
pids+=($!)

echo "→ starting UI dev server (:5173)…"
( cd "$UI_DIR" && npm run dev -- --host 0.0.0.0 ) &
pids+=($!)

sleep 3
echo "→ opening Cloudflare tunnel → http://localhost:5173"
echo "  (public URL appears below — share it WITH the access key)"
cloudflared tunnel --url http://localhost:5173
