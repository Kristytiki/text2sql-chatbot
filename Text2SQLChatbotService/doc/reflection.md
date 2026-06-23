# Reflection

> ## 🔗 Live demo access
>
> - **URL:** https://text2sql-chatbot.onrender.com/
> - **Access key:** `DlokgC3yS4tWsUVupcrrSby5f20axDeVECx1TutLb2E`
>
> On first load the app prompts for the access key — paste the key above. Then
> ask e.g. *"What's the population of California in 2020?"* Click **View SQL**
> under any answer to see the grounding query. First request after an idle
> period may take ~30–60s while the free-tier host and Snowflake warehouse wake
> up; subsequent requests are fast.

A written reflection on building the Census Text2SQL chat agent, as required by
the assignment. Covers the development process and key architectural decisions,
what I'd improve with more time, edge cases identified but not fully addressed,
and the testing approach.

---

## 1. Development process & key architectural decisions

### Two-stage architecture: semantic IR over direct text-to-SQL

The central decision was **not** to have the LLM write raw SQL against the
Snowflake tables. The flow is two-stage:

```
User ─► React UI (Vite) ─► FastAPI ─► Strands Agent (Bedrock Claude Sonnet)
                                          │  ① LLM stage (probabilistic)
                                          ▼
                              MetricFlowQueryRequest
                        {metric_names, group_by_names, where, time_*}
                                          │  ② Compiler stage (deterministic)
                                          ▼
                          MetricFlowEngine.explain() → Snowflake SQL
                                          │  sql_validator (sqlglot, allowlist)
                                          ▼
                              Snowflake (read-only role)
                                          │
                                          ▼
                          Result table → LLM summarizer → answer
```

The thesis: **in enterprise data-analysis settings, safety and controllability
outweigh expressiveness.** Direct SQL generation fails ~30–40% of the time in
production on three things — hallucinated column names, wrong JOIN paths, and
syntactically-valid-but-semantically-wrong SQL (the most dangerous: it returns
incorrect data with no error). Bounding the LLM to a **typed query request**
(metric/dimension names that must exist in the manifest) eliminates all three at
once, because MetricFlow — not the model — emits the SQL. The LLM never sees raw
table DDL; it reasons about *measures and dimensions*, a far smaller and safer
surface. The ACS data is also wide-format with two overlapping 5-year snapshots,
which direct generation routinely double-counts; the semantic layer freezes each
metric's definition once. (Full rationale in [design.md](./design.md).)

### Data scope: tiered build-out to "comprehensive mapping"

The assignment warns against covering only a subset. The full dataset is ~7,700
ACS metrics across 29 wide-format tables, named with leading digits (`B01001`,
`C24010`, …) that MetricFlow's identifiers reject. I built it out in three tiers
so coverage could grow without discarding early work:

- **Tier 1 — curated core (bootstrap).** 7 hand-picked schemas (population,
  race, hispanic, households, housing, poverty, employment) as manually authored
  MetricFlow-friendly views: tables renamed off their leading digits, columns
  aliased to readable measure names, plus the geography join hub. This de-risked
  the hard question — *does the LLM → semantic-query → compiler → SQL loop
  actually ground answers?* — on a small, trustworthy surface.
- **Tier 2 — full auto-generated catalog (current/deployed).** Once the loop was
  proven, `scripts/generate_catalog.py` introspects Snowflake and emits every
  semantic-model YAML for all 29 tables + geography (~7,760 metrics, 30 models).
  **No hand-written YAML remains** — the Tier-1 files were superseded by
  generated ones, so the system now covers the *complete* dataset, not a slice.
- **Tier 3 — margins of error (planned).** Surface the ACS margin-of-error / CI
  columns but **rank them lower in the prompt**, so the agent leads with point
  estimates and cites margins only when asked. Deferred because MoE is a
  precision refinement, not a coverage gap, and surfacing it prominently would
  clutter the common-case answer.

Building tiered rather than "full from day one" meant the generator was written
against a loop already known to work, and the deferral of Tier 3 is a deliberate
precision-vs-clutter trade-off, not an oversight.

### Layered guardrails, each at the right altitude

Rather than one big filter, defense is layered so each gate catches what it is
actually good at:

1. **Bedrock content filters** (managed) — profanity, PII, prompt-injection.
2. **Capability-boundary canned responses** (`guardrails/canned.py`) — the LLM
   emits a `[REFUSE:<category>]` tag for things it shouldn't do; the backend
   substitutes deterministic wording. This is where **off-topic** filtering
   now lives (`[REFUSE:off_topic]`).
3. **sqlglot SQL validator** — the hard boundary: rejects all DDL/DML, enforces
   a schema allowlist and a LIMIT cap. Even a fully jailbroken LLM cannot get a
   `DROP`/`DELETE` past it.
4. **Snowflake read-only role** (`CENSUS_READONLY` / `CENSUS_APP`) — least-
   privilege DB principal: `SELECT` on the published views only, no writes, no
   access to the underlying marketplace dataset.

### Deployment: single-origin container on Render, Bedrock for the LLM

The UI (React/Vite) is built and served by the FastAPI backend itself, so the
whole app is one origin (no CORS in prod). It runs as a Docker service on
Render, with a personal AWS account providing Bedrock (Claude Sonnet 4.5) via a
**least-privilege IAM user** scoped to `bedrock:InvokeModel` + `ApplyGuardrail`
+ the marketplace subscription check, nothing else. Access is gated by a shared
`X-API-Key`; the UI prompts for it and stores it client-side.

### Provider abstraction (Bedrock ↔ Anthropic)

The LLM provider is a flag (`LLM_PROVIDER`): `bedrock` by default, `anthropic`
as a drop-in alternative needing only an API key. This kept development on the
Amazon network unblocked while making a no-AWS deployment a one-line change. The
abstraction cost ~30 LOC and is a clean seam if Bedrock access ever becomes a
constraint.

---

## 2. Key decision changed mid-flight: off-topic filtering

**What I tried first.** Off-topic detection via a **Bedrock Guardrail "Denied
Topic"** with both input and output actions set to *Block*. On paper this is the
textbook "dedicated validation layer."

**Why I changed it.** In testing (Bedrock console, directly against the
guardrail), the Denied Topic **misfired on legitimate census queries** — e.g.
*"What's the population of California in 2020?"* was blocked. The root cause is
intrinsic to how Denied Topics work: they do **semantic** matching, and on the
**output** side a correct census answer is saturated with population / geography
/ demographic language that is hard to distinguish from "talking *about* the
topic of census." The false-positive rate on the core happy-path question was
unacceptable — the one query type the system most needs to answer was the one
it blocked.

**What I changed it to.** I removed the off-topic Denied Topic and moved
off-topic filtering into the **prompt + canned-tag layer** (`[REFUSE:off_topic]`).
There, the judgment is made by the LLM while *interpreting the user's intent*,
not by re-scanning the model's own grounded output — so a real answer is never
mistaken for an off-topic one. I **kept** the Bedrock **content filters**
(profanity/PII/prompt-injection), which operate on harmful-content categories
and do not misclassify legitimate data answers.

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

**Why this is a better trade-off.** It moved off-topic detection from a layer
with a high false-positive rate on the happy path to one with much better
precision, at no real cost to recall: genuinely off-topic inputs (weather,
sports, code help) still get a stable, branded refusal. The lesson generalized:
**put a filter at the altitude where its signal is cleanest** — content safety
at the managed-guardrail layer, intent/capability judgment at the LLM+canned
layer, and structural safety (no DDL, read-only) at the SQL/DB layer.

---

## 3. What I'd improve with more time

- **Streaming responses.** Replies are returned whole; the UI fakes progress
  with staged "thinking" labels. Real token streaming (Bedrock `ConverseStream`
  is already used under the hood) would improve perceived latency and is the
  right answer to the 60-second bar under load.
- **Session durability.** Sessions use Strands' `FileSessionManager` on local
  disk plus an in-process registry. On Render's free tier the disk is ephemeral
  and the instance spins down when idle, so conversation history does not
  survive a restart. A managed store (Redis / Postgres) would make multi-turn
  context durable.
- **Evaluation harness.** I have unit/integration tests (below) but no
  *answer-quality* evals — a fixed question set scored for correctness against
  known census figures. That's the highest-value thing I'd add next.
- **Tighter IAM.** The model resource is `anthropic.claude-*` (wildcard) to
  survive a model swap on a free-tier account; I'd pin it to the exact model ARN
  for a real deployment.
- **Guardrail telemetry.** The guardrail currently fails *open* (logs a warning,
  lets text through) on infra errors. For production I'd add a metric/alarm so a
  silently-degraded guardrail is visible, not just a log line.

---

## 4. Edge cases & failure modes identified

Handled:
- **Two overlapping 5-year snapshots (2019/2020).** Summing them double-counts
  population; the prompt forces a single-snapshot default and explains the
  overlap when asked to "average across years."
- **Non-additive metrics.** Medians/means can't be summed across census block
  groups; the catalog excludes them and the agent explains why
  (`[REFUSE:non_additive_metric]`).
- **Capability vs. topic confusion.** *"What's the population of CA?"* (answer)
  vs. *"Generate a synthetic CA census"* (refuse) — both are on-topic, only one
  is in-capability. The canned-tag layer separates these.
- **Graceful degradation.** Upstream model errors return a friendly 502 message
  and roll the session history back to its pre-call baseline so a half-formed
  tool call doesn't poison subsequent turns.

Identified but **not** fully addressed:
- **Ambiguous geographic granularity.** "California" → state aggregate is
  inferred, but "the Bay Area" (no clean FIPS boundary) isn't handled well.
- **Cross-snapshot comparison phrasing.** "Compare 2019 vs 2020" works; subtler
  framings ("how did it change") sometimes pick one snapshot silently.
- **Concurrency under the free tier.** Per-session locking is correct, but I
  haven't load-tested many concurrent sessions against the 512 MB instance.
- **Guardrail false-negatives.** Having removed the off-topic Denied Topic, a
  cleverly phrased off-topic prompt that the LLM doesn't tag would slip through
  to a (likely unhelpful) answer rather than a clean refusal. I judged the
  precision gain worth this small recall risk; an eval set would quantify it.

---

## 5. Testing approach

The suite (`tests/`, **32 tests, all passing**, fully offline — no Snowflake or
Bedrock calls) targets the highest-risk, most-regressable logic rather than
chasing a coverage number:

| Area | What's pinned |
|---|---|
| **SQL validator** (`test_sql_validator.py`) | DDL/DML rejection, stacked-statement injection, schema/database allowlist, LIMIT capping, CTE handling |
| **API-key auth** (`test_auth.py`) | correct / wrong / missing key → 401; empty config → dev no-op |
| **LLM provider flag** (`test_provider.py`) | bedrock builds offline, anthropic guards on missing key, unknown provider rejected |
| **Evidence extraction** (`test_evidence.py`) | per-turn `semantic_query` tool pairing; no leakage of prior-turn SQL |
| **HTTP layer** (`test_http.py`) | TestClient: `/health`, the 401 auth flow, unknown-session 404 |

**Writing the validator tests surfaced a real bug.** A test for "reject a LIMIT
above the cap" failed — because `SqlValidationError` subclasses `ValueError`,
the raise sat inside a `try/except ValueError` that swallowed it, so oversized
LIMITs passed silently and the row cap was effectively dead. Moving the raise
out of the `try` fixed it. This is exactly the kind of latent security bug that
tests exist to catch, and it justified the time spent on them.

**What I'd add:** the answer-quality eval set mentioned above, a small set of
adversarial prompt-injection cases run against the live agent, and UI component
tests for the access-key gate and error states.
