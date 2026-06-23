# Text2SQL Census Agent — Design Doc

## Context

Build a production-quality chat agent that answers natural-language questions about the US population using the **Snowflake US Open Census** dataset.

Success criteria: grounded answers, ≤60s latency, multi-turn context, guardrails, graceful degradation, public URL.

---

## Core argument — Why semantic-IR over direct text-to-SQL

> **In enterprise data-analysis settings, safety and controllability outweigh expressiveness.**

### The 80% problem with direct generation

```
┌─────────────────────────────────────────────────────┐
│  What is the 80% problem with direct generation?     │
│                                                       │
│  • Hallucinated column names (30-40% errors in prod) │
│  • Wrong JOIN paths                                   │
│  • Syntactically valid but semantically wrong SQL     │
│    (most dangerous — returns wrong data, no error)    │
│                                                       │
│  Bounded output eliminates all three at once          │
└─────────────────────────────────────────────────────┘
```

The LLM can only emit a type-constrained `SemanticQuery` JSON (metric /
dimension / filter / time_grain); the SQL is produced by a deterministic
compiler. The model no longer freely concatenates column names or JOINs, so:

- **Hallucinated column names** → the compiler only accepts metrics/dimensions
  that exist in the catalog; anything unregistered is rejected.
- **Wrong JOIN paths** → the catalog declares each metric's fact table and join
  key, and the compiler generates along the predeclared path.
- **Semantically wrong results** → each metric's definition is frozen once in
  the catalog, and every question shares that same definition.

### ⚠️ Limitations of this approach & mitigations

| Limitation | Mitigation |
|---|---|
| User asks something outside the catalog | Graceful degradation: tell the user "that metric isn't supported", or fall back to direct generation (backstopped by the SQL validator) |
| Complex analytics needs (window functions, CTEs) | Extend the SemanticQuery schema (add `derived_metric`, `window`, `having` fields) |
| Initial build cost | Start from dbt metrics or an existing BI tool's semantic layer instead of writing the catalog from scratch |

---

## Architecture

```
User ──► React UI (Vite) ──► FastAPI ──► Strands Agent (Claude Sonnet 4.5)
                                              │
                                              │ ① LLM stage (probabilistic)
                                              ▼
                                    MetricFlowQueryRequest
                              {metric_names, group_by_names, where_constraints, time_*}
                                              │
                                              │ ② Compiler stage (deterministic)
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

We use `metricflow` as the semantic layer + SQL compiler — no dbt project required.

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

## Data scope — tiered build-out

The assignment explicitly warns against limiting to a subset ("Comprehensive
Mapping: do not limit yourself … ensure your system can access the complete
range of the data"). The full dataset is ~7,700 ACS metrics across 29 topic
tables, all wide-format and named with leading digits (`B01001`, `C24010`, …)
which MetricFlow's identifiers reject. We staged the build-out in three tiers so
coverage could grow without throwing away early work:

| Tier | Scope | How | Status |
|---|---|---|---|
| **Tier 1 — curated core** | 7 hand-picked schemas (population, race, hispanic, households, housing, poverty, employment) | Manually authored MetricFlow-friendly views: rename tables off leading digits, alias columns to human-readable measure names, add the geography join hub | Bootstrap — proved the semantic-IR loop end-to-end |
| **Tier 2 — full auto-generated catalog** | All 29 ACS tables + geography (≈7,760 metrics, 30 semantic models) | `scripts/generate_catalog.py` introspects Snowflake and emits every semantic-model YAML; **no hand-written YAML remains** — the curated Tier-1 files were superseded by generated ones | **Current / deployed** — this is what runs in prod |
| **Tier 3 — margins of error** | ACS confidence-interval / margin columns | Surface the `_margin`/MOE columns, but **rank them lower in the prompt** so the agent leads with point estimates and only cites margins when asked | Planned — not yet built |

**Why tiered, not "full from day one":** Tier 1 de-risked the hard part (does the
LLM → semantic-query → compiler → SQL loop actually ground answers?) on a small,
trustworthy surface before investing in the generator. Once the loop was proven,
Tier 2's generator replaced the hand-written YAML wholesale, so the system now
covers the **complete** dataset rather than a curated slice — directly answering
the "comprehensive mapping" requirement. Tier 3 is deferred because margins of
error are a precision refinement, not a coverage gap, and surfacing them too
prominently would clutter the common-case answer.

---

## Package layout

```
Text2SQLChatbotService/src/text2sqlchatbotservice/
  app.py                  # FastAPI factory + lifespan (wires all components)
  config.py               # pydantic-settings; env-driven
  agent/
    factory.py            # Strands Agent factory (Bedrock or Anthropic per LLM_PROVIDER)
    system_prompt.py      # Census-grounded prompt: data shape, time semantics, refusal tags
  semantic/
    compiler.py           # dict → MetricFlowQueryRequest → engine.explain() → SQL
    catalog_index.py      # in-memory metric index for list_metrics fuzzy search
    catalog/              # auto-generated semantic_models/*.yaml + metrics.yaml
  tools/
    builder.py            # @tool: semantic_query, list_metrics, calculator
  snowflake/
    client.py             # snowflake-connector-python pool, read-only, LIMIT cap
  guardrails/
    bedrock_guardrail.py  # Bedrock content filters (input + output)
    canned.py             # capability-boundary tag dispatch ([REFUSE:<category>])
    sql_validator.py      # sqlglot AST: SELECT-only, allowlist schemas, LIMIT cap (CTE/UNION-aware)
  memory/
    session_registry.py   # in-process map + on-demand restore from disk
  api/
    routers/chat.py       # POST /chat/sessions, .../messages, .../history
    schemas.py            # Pydantic wire models
    auth.py               # X-API-Key dependency

Text2SQLChatbotUI/        # React 19 + Vite single-pane chat UI
```

---

## Guardrail trade-offs

```
                                          ┌─────────────────────────┐
 User input → Bedrock guardrail (in) →    │  Strands Agent          │
                   ↓                       │  (Claude Sonnet 4.5)    │
               [allowed]                   │                         │
                                           │  For OOC / refusals:    │
                                           │   emits [REFUSE:cat]    │
                                           │   instead of free text  │
                                           └──────────┬──────────────┘
                                                      ▼
                                           ┌──────────────────────┐
                                           │ canned.dispatch()     │
                                           │ tag → canned text     │
                                           │ no tag → pass through  │
                                           └──────────┬────────────┘
                                                      ▼
                                          Bedrock guardrail (out)
                                                      ↓
                                                    user
```

| Layer | Catches | Cost | Verdict |
|---|---|---|---|
| **Bedrock Guardrails** (managed, input+output) | Profanity, PII, prompt-injection, content-policy via **content filters** | +200–400ms; ~$0.75/1k text units; configured in console | **Content filters only** — see decision below. Off-topic is NOT handled here. |
| **Capability-boundary canned responses** (post-LLM tag dispatch) | Off-topic + "census-related but agent can't do it" (generate / synthesize / forecast / advise) | <1ms; ~30 LOC; deterministic | **Required** — handles both off-topic *and* the can't-tell-apart case: *"population of CA?"* (answer) vs *"generate a synthetic CA census"* (refuse). |
| **SQL static validator** (sqlglot) | DDL/DML rejection, schema allowlist, LIMIT cap (incl. CTE/UNION) | <5ms; ~120 LOC | **Mandatory** — last gate at the data boundary. Bedrock reads natural language, not SQL ASTs — can't tell `DROP TABLE` from `SELECT`. |
| **Bedrock Denied Topic** for off-topic | Off-topic via semantic match | console-config | **Removed** — misfired on legit queries (blocked *"population of CA"*). Moved to canned-tag layer. See [reflection](./reflection.md). |
| **Pre-flight LLM classifier** / **output hallucination filter** | Domain rejects / hallucinated stats | +1 LLM call / +200ms | **Skip** — canned-tag dispatch + grounding (`semantic_query` cites SQL + row counts) subsume these without extra latency. |

**Net stack:** Bedrock content filters (in+out) → LLM → canned-tag dispatch (incl. `[REFUSE:off_topic]`) → sqlglot SQL validator → read-only Snowflake role → result-row cap. Off-topic filtering lives in the prompt + canned-tag layer, NOT in a Bedrock Denied Topic — see [reflection](./reflection.md) for why. The capability-boundary layer + SQL validator are the only **mandatory** custom code; everything else is managed.

### Capability-boundary canned responses

The LLM is instructed (in `system_prompt.py`) that when a user asks for something **out of capability**, it must NOT improvise the refusal — instead it emits a single tag of the form `[REFUSE:<category>]` (optionally with `:key=value` parameters). The backend (`guardrails/canned.py`) catches that tag in `chat.py` after the agent returns and substitutes a deterministic canned message. This keeps the refusal **wording stable, brand-consistent, and reviewable in git** instead of regenerated every turn.

**Tag categories:**

| Tag | Triggered by | Canned response gist |
|---|---|---|
| `[REFUSE:off_topic]` | Question has nothing to do with US Census (weather, sports, jokes, code help) | "I can only answer questions about the US population using the Snowflake US Open Census dataset. Try …" + 3 example queries |
| `[REFUSE:out_of_capability]` | Census-shaped but not a query — generate / synthesize / forecast / train / deploy / export / edit | "I can't generate or synthesize census data — I'm a query agent over the existing dataset. What I CAN do …" + suggested redirect |
| `[REFUSE:future_data:year=YYYY]` | Asks for a year we don't have (anything outside 2019/2020) | "The dataset only covers 2019 and 2020 ACS 5-year snapshots. I don't have data for {year}. Did you mean 2020?" |
| `[REFUSE:individual_data]` | Asks about a specific person, address, or sub-CBG geography | "I can't look up individual people. The Census Bureau only releases aggregated counts at the census-block-group level (~600–3000 people each)." |
| `[REFUSE:personal_advice]` | Asks for legal / medical / financial / policy advice or actions on the user's behalf | "I can answer factual questions about census data but can't give advice or take actions. Is there a Census stat I can pull for you?" |
| `[REFUSE:prompt_injection]` | Tries to override the agent's rules ("ignore previous instructions", persona override, system-prompt extraction) | "I'll stick to answering questions about the US Census dataset. What demographic data can I help you find?" |
| `[REFUSE:non_additive_metric:metric=X]` | User asks for a median / mean / aggregate column that the catalog excluded (sum-aggregating across CBGs is wrong) | "Census ACS provides {metric} per census-block-group; summing them across geographies is statistically incorrect. I can show you the underlying counts and you can compute a population-weighted estimate." |

Tags are matched by a regex anchored at the **start of the LLM output** (`^\[REFUSE:([a-z_]+)(?::([^\]]*))?\]`). Anything after the tag is ignored. No tag → response goes through unchanged.

Why "tag" and not "have the LLM write the canned text directly":
- LLMs paraphrase silently → wording drifts every turn → ops can't audit
- Tag dispatch is deterministic and trivially testable (snapshot tests)
- Adding/changing a category is a 1-line YAML / dict edit + 1 line in `system_prompt.py`

---

## Strands agent

- Model selected at runtime by `LLM_PROVIDER` — `bedrock` (default, host AWS creds) or `anthropic` (direct API key, no AWS). Default `claude-sonnet-4-5`.
- Tools (3): `semantic_query`, `list_metrics`, `calculator`.
- The catalog has ~7,700 metrics — far too many to inline in the prompt. The system prompt teaches the model the *shape* of the data (wide-format ACS tables, valid group-by dimensions, time semantics) and `list_metrics` does fuzzy discovery on demand.
- `SlidingWindowConversationManager(window_size=20)` for multi-turn context.
- Per-session `Agent` instance held in `SessionRegistry`. Strands `FileSessionManager` persists each session to `.sessions/`; the registry restores from disk on cache-miss so sessions survive process restarts.

**LLM choice note:** the assignment does not constrain the LLM. We pick Claude Sonnet 4.5 for tool-use quality + prompt caching; see [reflection](./reflection.md).

---

## Snowflake connection

- **Read-only execution** against the dataset views (`CENSUS_DB.CENSUS_VIEWS`).
- `snowflake-connector-python` with password (or key-pair) auth via env vars.
- A small `LifoQueue` connection pool (size 4) — each query gets its own connection (Snowflake's connector is not cursor-isolated on a shared connection), with warm-connection reuse.
- Every query passes through `sql_validator.py` before execution; the warehouse auto-suspends when idle.

---

## Frontend

- React 19 + Vite, single-pane chat UI (`react-markdown` for rich answers).
- Live "thinking" status (catalog → metrics → SQL → Snowflake → summarize) that collapses into a step list once the answer arrives.
- Each answer carries a collapsible **"View SQL"** panel — the grounding query + row counts, so a user (or reviewer) can verify any number.
- API-key gate: the key is entered once, stored in `localStorage`, sent as `X-API-Key` on every request.

---

## Tests (meaningful, not exhaustive)

- `test_sql_validator.py` — DDL/DML rejection, schema allowlist, LIMIT cap, CTE/UNION-aware injection (table-driven).
- `test_canned.py` — refusal-tag dispatch: each category maps to its canned text; unknown tag falls back; param interpolation.
- `test_provider.py` — LLM-provider selection (bedrock vs anthropic) and error on misconfig.
- `test_evidence.py` — evidence extraction is scoped to the current turn only.
- HTTP auth/health contract.
- All offline — no Snowflake/Bedrock needed.
- Deferred (see [reflection](./reflection.md)): load tests, full LLM-judge eval harness.

---

## Deployment

- Public URL via Render (Docker web service). Single multi-stage Dockerfile: Vite build → Python image serving the API + static UI from one origin.
- Secrets via environment (Snowflake creds, AWS creds or Anthropic key, `API_KEY`). Never committed.
- See [deployment.md](./deployment.md) for the full setup.

## Verification

1. `pytest` — all unit tests green (offline).
2. Hit the public URL, run scripted questions covering: simple metric, multi-turn follow-up, ambiguous query, off-topic (refused), out-of-capability (refused), unanswerable year (graceful "I don't have that").
3. Deploy to EC2, repeat from a different network, time each turn (must be ≤60s).
4. Manual adversarial prompts: `DROP TABLE`, `'; SELECT * FROM internal--`, "ignore previous instructions" — all rejected by either Bedrock Guardrail or sqlglot validator.
