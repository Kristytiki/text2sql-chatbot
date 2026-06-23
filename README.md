# Census Text2SQL Chatbot

A natural-language chat agent over the Snowflake US Open Census dataset. The LLM
emits a typed semantic query; a deterministic compiler (MetricFlow) — not the
model — produces the SQL, which is validated and run read-only against Snowflake.

## 🔗 Live demo

> Reviewers: no local setup required.

- **URL:** https://text2sql-chatbot.onrender.com/
- **Access key:** `DlokgC3yS4tWsUVupcrrSby5f20axDeVECx1TutLb2E`

Paste the key when prompted, then ask e.g. *"What's the population of California
in 2020?"* Click **View SQL** to see the grounding query. First request after
idle may take ~30–60s (free-tier cold start).

## Repo map

| Path | What's there |
|---|---|
| [`Text2SQLChatbotService/`](Text2SQLChatbotService/) | FastAPI backend, semantic layer, guardrails, tests |
| [`Text2SQLChatbotUI/`](Text2SQLChatbotUI/) | React + Vite chat UI |
| [`Dockerfile`](Dockerfile) / [`render.yaml`](render.yaml) | Single-origin container + Render deploy |

## Documents

- **[Reflection](Text2SQLChatbotService/doc/reflection.md)** — process, key decisions, trade-offs, edge cases, testing
- **[Design](Text2SQLChatbotService/doc/design.md)** — architecture & rationale
- **[Deployment](Text2SQLChatbotService/doc/deployment.md)** — AWS + Render setup
