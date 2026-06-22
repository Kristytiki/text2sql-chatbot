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

See [doc/design.md](../Text2SQLAgent/doc/design.md) for architecture.
