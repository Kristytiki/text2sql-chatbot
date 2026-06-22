# Multi-stage build: build the React UI, then run the FastAPI backend which
# serves the built UI at "/" (single origin — no CORS in production).
#
# Build context = repo root (SQLAgent/), so both subprojects are visible.

# ── Stage 1: build the UI ───────────────────────────────────────────────────
FROM node:22-slim AS ui
WORKDIR /ui
COPY Text2SQLChatbotUI/package.json Text2SQLChatbotUI/package-lock.json ./
RUN npm ci
COPY Text2SQLChatbotUI/ ./
# Same-origin in prod: leave VITE_API_BASE empty so the UI calls relative
# /chat and /health against the host that served it.
RUN npm run build   # → /ui/dist

# ── Stage 2: Python backend + bundled UI ────────────────────────────────────
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# Install the backend package.
COPY Text2SQLChatbotService/pyproject.toml ./
COPY Text2SQLChatbotService/src ./src
RUN pip install --upgrade pip && pip install .

# Bring in the built UI and point the backend at it.
COPY --from=ui /ui/dist ./ui_dist
ENV UI_DIST_DIR=/app/ui_dist \
    HOST=0.0.0.0

# Render injects $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# `text2sqlchatbotservice` console script calls app.run(), which reads HOST/PORT.
CMD ["text2sqlchatbotservice"]
