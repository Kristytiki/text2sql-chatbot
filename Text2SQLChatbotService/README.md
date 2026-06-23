# Text2SQLChatbotService

Natural-language chat agent over the Snowflake US Open Census dataset. The LLM
emits a typed semantic query; a deterministic compiler (MetricFlow) — not the
model — produces the SQL, which is validated and run read-only against Snowflake.

## 🔗 Live demo

> Reviewers: no local setup required.

- **URL:** https://text2sql-chatbot.onrender.com/
- **Access key:** `DlokgC3yS4tWsUVupcrrSby5f20axDeVECx1TutLb2E`

Paste the key when prompted, then ask e.g. *"What's the population of California
in 2020?"* Click **View SQL** to see the grounding query. The first request
after idle may take ~30–60s (free-tier cold start).

## Layout

```
src/text2sqlchatbotservice/
  api/          FastAPI routers + Pydantic wire schemas
  agent/        Strands Agent factory (Bedrock / Anthropic — see LLM_PROVIDER)
  semantic/     MetricFlow manifest loader + compiler wrapper, catalog/*.yaml
  snowflake/    Snowflake connection pool + read-only execution
  guardrails/   Bedrock content filters, capability-boundary canned responses, sqlglot SQL validator
  memory/       SessionRegistry (in-process + on-demand restore)
  tools/        @tool functions: semantic_query, list_metrics, calculator
```

## Run locally

```bash
cp .env.example .env   # fill SNOWFLAKE_PASSWORD (+ AWS creds for Bedrock)
pip install -e .
text2sqlchatbotservice  # → http://127.0.0.1:8000
```

Run the UI separately (`Text2SQLChatbotUI`, `npm run dev`), or set `UI_DIST_DIR`
to a built `dist/` to serve both from this one process (single origin).

## Tests

```bash
pip install -e ".[test]"
pytest   # 32 tests, fully offline
```

## More

- [doc/design.md](doc/design.md) — architecture & key decisions
- [doc/reflection.md](doc/reflection.md) — written reflection (process, trade-offs, edge cases, testing)
- [doc/deployment.md](doc/deployment.md) — AWS + Render setup
