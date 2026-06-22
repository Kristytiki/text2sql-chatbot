# Text2SQLAgent — Design Doc

## Context

Build a production-quality chat agent that answers natural-language questions about the US population using the **Snowflake US Open Census** dataset (24h take-home for Snowflake interview). The skeleton package `Text2SQLAgent` is empty. We will reuse proven patterns from two local packages:

- `AMXTalentPluginAgentCore` — Strands agent loop, Bedrock memory hooks, guardrails.
- `RolePlayAgent` — React + Vite UI + FastAPI backend, server-side session registry.

Success criteria: grounded answers, ≤60s latency, multi-turn context, guardrails, graceful degradation, public URL.

---

## 核心论点 — Why semantic-IR over direct text-to-SQL

> **企业级数据分析场景中，安全性和可控性 > 表达力。**

### Direct-generation 方案的 80% 问题

```
┌─────────────────────────────────────────────────────┐
│  直接生成方案的 80% 问题是什么？                     │
│                                                     │
│  • 幻觉列名 (30-40% errors in production)           │
│  • 错误 JOIN 路径                                   │
│  • SQL 语法对但语义错（最危险 — 返回错误数据无报错） │
│                                                     │
│  本方案通过 bounded output 一次性消除这三类问题      │
└─────────────────────────────────────────────────────┘
```

LLM 只能输出受类型约束的 `SemanticQuery` JSON（metric / dimension / filter / time_grain），SQL 由确定性编译器生成。模型不再自由拼接列名或 JOIN，因此：

- **幻觉列名** → 编译器只接受 catalog 内的 metric/dimension，未注册即拒绝。
- **错误 JOIN 路径** → catalog 已声明每个 metric 的 fact 表与 join key，编译器按既定路径生成。
- **语义对但结果错** → 每条 metric 的口径在 catalog 中冻结一次，所有问句共享同一份口径。

### ⚠️ 本方案的局限 & 应对

| 局限 | 应对策略 |
|---|---|
| 用户问超出 catalog 的问题 | 优雅降级：提示"当前不支持该指标"，或 fallback 到直接生成模式（受 SQL validator 兜底） |
| 复杂分析需求（窗口函数、CTE） | 扩展 SemanticQuery schema（加 `derived_metric`、`window`、`having` 字段） |
| 初始建设成本 | 以 dbt metrics 或现有 BI 工具的 semantic layer 为起点，不从零写 catalog |

---

## Architecture (matches reference screenshot)

```
User ──► React UI (Vite) ──► FastAPI ──► Strands Agent (Bedrock Claude Sonnet 4.6)
                                              │
                                              │ ① LLM stage (概率推理)
                                              ▼
                                    MetricFlowQueryRequest
                              {metric_names, group_by_names, where_constraints, time_*}
                                              │
                                              │ ② Compiler stage (确定性翻译)
                                              ▼
                                MetricFlowEngine.explain() → Snowflake SQL
                                              │
                                              │ sql_validator (sqlglot, allowlist)
                                              ▼
                                  Snowflake connector (read-only role)
                                              │
                                              ▼
                                       Result table → LLM summarizer → answer
```

Two-stage design (LLM → typed query request → deterministic compiler → SQL) is the safest path for text2sql: the LLM emits a `MetricFlowQueryRequest` (metric names + dimension names from the manifest), and **MetricFlow** — not the model — emits the SQL.

### Semantic-layer choice: MetricFlow (standalone)

We use `metricflow` from `/home/zheqi/workspace/SQLAgent/metricflow` as the semantic layer + SQL compiler — no dbt project required.

- **Manifest** built from our own YAML via `parse_directory_of_yaml_files_to_semantic_manifest(...)` → `PydanticSemanticManifest` → `SemanticManifestLookup`.
- **Compiler** = `MetricFlowEngine.explain(MetricFlowQueryRequest.create(metric_names=[...], group_by_names=[...], where_constraints=[...]))`. Returns SQL without executing.
- **Snowflake dialect** = `SnowflakeSqlPlanRenderer` (built in).
- **Execution** is decoupled: we run the returned SQL ourselves through `snowflake-connector-python` (read-only role) after `sql_validator` passes it. This keeps MetricFlow off the network path and makes unit testing trivial (`explain()` is pure).

Why MetricFlow vs. our own compiler vs. Cube.dev:

| Option | Verdict |
|---|---|
| `dbt-semantic-interfaces` only (parser/validator) | Insufficient — no SQL generation. |
| **MetricFlow standalone** | ✅ Battle-tested compiler, Snowflake dialect, light deps (Jinja2/pydantic/sqlglot — no SQLAlchemy or dbt-core in base install), works without a dbt project. |
| Cube.dev | Adds a Node.js service — extra deploy surface for 24h. |
| Hand-rolled compiler | Re-invents what MetricFlow already does correctly. |

---

## Package layout

```
src/text2_sql_agent/
  agent/
    agent.py              # Strands Agent factory (copy single_agent.py pattern)
    system_prompt.py      # Census-grounded prompt + schema digest
  semantic/
    manifest_loader.py    # YAML dir → PydanticSemanticManifest → SemanticManifestLookup
    catalog.yaml/         # hand-curated semantic_models + metrics (Census)
    compiler.py           # thin wrapper: dict → MetricFlowQueryRequest → engine.explain() → SQL
  tools/
    semantic_query_tool.py   # @tool: takes SemanticQuery, returns rows + sql
    list_metrics_tool.py     # @tool: search the catalog (RAG-lite)
    calculator_tool.py       # @tool: post-query math (per-capita, growth, ratio)
  snowflake/
    client.py             # snowflake-connector-python, read-only role, LIMIT cap
  guardrails/
    bedrock_guardrail.py  # Bedrock Guardrails (input + output)
    sql_validator.py      # sqlglot AST: SELECT-only, allowlist schemas, LIMIT≤10k
  memory/
    session_registry.py   # in-process dict[session_id] -> Agent
  api/
    app.py                # FastAPI: POST /chat/sessions, .../messages (SSE)
ui/                       # React + Vite (copy RolePlayChatbotUI)
```

---

## Guardrail trade-offs

| Layer | Catches | Cost | Verdict |
|---|---|---|---|
| **Bedrock Guardrails** (managed) | Off-topic, profanity, PII, prompt-injection, denied topics; input + output in one API call | +200–400ms; ~$0.75/1k text units; configured in console | **Primary**. Covers ~80% of pre-flight + output-filter, with one config and AWS-managed updates. |
| **Pre-flight LLM classifier** (custom) | Domain-specific rejects with canned replies | +1 LLM call (~500ms) | **Skip** — Bedrock Guardrails' "Denied Topics" subsumes this without an extra LLM call. |
| **Token-level / SQL static validator** (sqlglot) | DDL/DML rejection, schema allowlist, LIMIT injection, join-bomb detection | <5ms; ~80 LOC | **Mandatory**. Bedrock operates on natural language, not SQL ASTs — cannot tell `DROP TABLE` from `SELECT`. |
| **Output post-filter** (custom) | Hallucinated stats, leaked schema names | +200ms or regex | **Skip** — Bedrock output guardrail covers PII/profanity; hallucination is fought with grounding (cite SQL + row counts), not a post-filter. |

**Net stack:** Bedrock Guardrails (in+out) + sqlglot SQL validator + read-only Snowflake role + result-row cap. SQL validator is non-negotiable; the rest stays declarative.

---

## Strands agent

Copy `create_agent()` pattern from `AMXTalentPluginAgentCore/.../agents/single_agent.py`:
- `BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", cache_config=CacheConfig(strategy="auto"))`
- Tools (3): `semantic_query`, `list_metrics`, `calculator`
- System prompt embeds the metric catalog (~30 metrics × 1 line) so the model picks a metric without an extra retrieval call. Long tail handled by `list_metrics` fuzzy search.
- `SlidingWindowConversationManager(window_size=20)` for multi-turn context.
- Per-session `Agent` instance kept in `SessionRegistry` (RolePlayAgent pattern). No Bedrock AgentCore Memory — in-process dict + persistence to `.sessions/` is sufficient for 24h scope.

**LLM choice note:** requirement.md does not constrain the LLM. We pick Bedrock Claude Sonnet 4.6 for tool-use quality + prompt caching; documented in REFLECTION.

---

## Snowflake connection

- Free trial account; provision a **read-only role** (`USAGE` on warehouse + `SELECT` on the marketplace `US_OPEN_CENSUS_DATA` schema).
- `snowflake-connector-python` with private-key or password auth via env vars.
- Connection pool (size 4) created at FastAPI startup.
- Every query goes through `sql_validator.py` first; warehouse auto-suspends after 60s.
- Catalog (`catalog.py`) hand-curated against the dataset's `CBG_*`, `CENSUS_BLOCK_GROUPS_*`, `CBG_GEOGRAPHIC_DATA` tables — small enough to fit in the system prompt.

---

## Frontend (RolePlayAgent pattern)

- Copy `RolePlayChatbotUI/` (React 19 + Vite + plain CSS) and trim persona-picker → single chat pane.
- Add SSE for token streaming (RolePlayAgent does NOT stream — added here for the 60s SLA + UX). FastAPI endpoint returns `text/event-stream`.
- Render: streamed tokens, then a collapsible **"View SQL + rows"** panel — grounding evidence for the user (and the reviewer).

---

## Tests (meaningful, not exhaustive)

- `test_compiler.py` — semantic IR → SQL golden snapshots (10 cases).
- `test_sql_validator.py` — reject DDL/DML/UNION-injection/non-allowlisted-schema (table-driven).
- `test_agent_e2e.py` — 6–8 question fixtures hitting a mocked Snowflake; asserts correct metric pick + grounded answer.
- `test_guardrails.py` — off-topic, prompt-injection, SQL-injection-in-NL all rejected.
- Defer (call out in REFLECTION): load tests, full LLM-judge eval harness.

---

## Deployment

- Public URL: **EC2 t3.small** (fallback: Streamlit Cloud if we drop React for Streamlit). Caddy in front for HTTPS + basic auth, credentials in README.
- Secrets via `.env` on the host (Snowflake creds, AWS creds, Bedrock Guardrail ARN). Never committed.
- Single Dockerfile, multi-stage: Vite build → FastAPI image serving static files.

---

## Critical files to create

- `src/text2_sql_agent/semantic/manifest_loader.py`, `compiler.py`, `catalog/*.yaml` — manifest + MetricFlow wrapper.
- `src/text2_sql_agent/tools/semantic_query_tool.py` — primary agent tool.
- `src/text2_sql_agent/guardrails/sql_validator.py` — sqlglot validation.
- `src/text2_sql_agent/agent/agent.py` — Strands wiring.
- `src/text2_sql_agent/api/app.py` — FastAPI app.
- `ui/` — React + Vite.
- `Dockerfile`, `README.md`, `REFLECTION.md`.

## Reuse references

- Strands agent loop: `/home/zheqi/workspace/AMXTalentPluginAgentCore/src/AMXTalentPluginAgentCore/src/amx_talent_plugin_agent_core/agents/single_agent.py`
- FastAPI + session registry: `/home/zheqi/workspace/RolePlayAgent/RolePlayChatbotService/`
- React UI: `/home/zheqi/workspace/RolePlayAgent/RolePlayChatbotUI/`
- MetricFlow engine: `/home/zheqi/workspace/SQLAgent/metricflow/metricflow/engine/metricflow_engine.py` (`MetricFlowEngine`, `MetricFlowQueryRequest`, `MetricFlowExplainResult`)
- Snowflake renderer: `/home/zheqi/workspace/SQLAgent/metricflow/metricflow/sql/render/snowflake.py`
- Manifest parsing: `/home/zheqi/workspace/SQLAgent/metricflow/metricflow_semantic_interfaces/parsing/dir_to_model.py`

## Verification

1. `pytest -q` — all unit + e2e tests green.
2. `docker compose up`, hit local URL, run 5 scripted questions covering: simple metric, multi-turn follow-up, ambiguous query, off-topic (guardrail), unanswerable (graceful "I don't have that").
3. Deploy to EC2, repeat from a different network, time each turn (must be ≤60s).
4. Manual adversarial prompts: `DROP TABLE`, `'; SELECT * FROM internal--`, "ignore previous instructions" — all rejected by either Bedrock Guardrail or sqlglot validator.
