# Text2SQLChatbotService

FastAPI backend for the Text2SQL census agent.

```
src/text2sqlchatbotservice/
  api/          FastAPI routers + Pydantic wire schemas
  agent/        Strands Agent factory (Bedrock Claude Sonnet)
  semantic/     MetricFlow manifest loader + compiler wrapper, catalog/*.yaml
  snowflake/    Snowflake connection pool + read-only execution
  guardrails/   Bedrock Guardrails wrapper, sqlglot SQL validator
  memory/       In-process SessionRegistry
  tools/        @tool functions: semantic_query, list_metrics, calculator
```

## Run locally

```bash
cp .env.example .env  # fill SNOWFLAKE_PASSWORD, BEDROCK_GUARDRAIL_ID
pip install -e .
text2sqlchatbotservice  # → http://127.0.0.1:8000
```

Run the UI separately (`Text2SQLChatbotUI`, `npm run dev`), or set `UI_DIST_DIR`
to a built `dist/` to serve both from this one process (single origin).

## Tests

```bash
pip install -e ".[test]"
pytest
```

Covers the SQL validator (DDL/DML rejection, allowlists, LIMIT capping), the
API-key auth boundary, LLM-provider selection, evidence extraction, and the
HTTP auth/health contract — all offline (no Snowflake/Bedrock needed).

## Accessing the deployed demo

> **Reviewers:** the running web app is here. No local setup required.

- **URL:** `<FILL IN AFTER DEPLOY — e.g. https://census-chatbot.onrender.com>`
- **Access key:** `<FILL IN — the API_KEY value>`

On first load the app prompts for the access key. Paste the key above; it is
stored in your browser and sent on every request. Then ask questions like
*"What's the population of California in 2020?"* Off-topic questions are
declined by the guardrail. Click **View SQL** under any answer to see the
grounding query.

> First request after an idle period may take ~30–60s while the free-tier host
> and Snowflake warehouse wake up. Subsequent requests are fast.

## Deployment

See [doc/deployment.md](doc/deployment.md) for the full AWS + Render setup.

See [doc/design.md](../Text2SQLAgent/doc/design.md) for architecture.
