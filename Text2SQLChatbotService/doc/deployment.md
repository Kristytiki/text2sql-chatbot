# Deployment — public demo on Render

Target: a private demo URL for ≤4 reviewers, ~1 week, then torn down.

Architecture stays single-origin: Render runs the FastAPI backend, which also
serves the built UI. LLM **and** the off-topic guardrail both run on **AWS
Bedrock** (personal account, region **us-east-1**). Snowflake is accessed
through a least-privilege read-only role.

```
reviewer ──https──> Render service ──> Bedrock (LLM + ApplyGuardrail)  [us-east-1]
                          │
                          └──────────> Snowflake (CENSUS_APP, read-only)
```

Three things must exist in AWS before deploying. Do them in order.

---

## 1. Enable Bedrock model access (us-east-1)

Free-plan accounts still need to explicitly request model access.

1. Console → switch region to **US East (N. Virginia) us-east-1** (top-right).
2. Open **Amazon Bedrock** → left nav **Model access** → **Modify model access**.
3. Enable **Anthropic – Claude Sonnet 4.5** (the model behind
   `us.anthropic.claude-sonnet-4-5-20250929-v1:0`). Submit.
4. Wait until status = **Access granted** (usually instant for Anthropic).

> If Claude Sonnet 4.5 isn't offered in us-east-1 on a free plan, enable the
> latest Claude Sonnet that *is* available and set `BEDROCK_MODEL_ID` to its
> inference-profile id. The `us.` prefix means a cross-region inference profile
> — confirm it's listed under **Cross-region inference** in the console.

---

## 2. Create the Bedrock Guardrail (off-topic + safety filter)

This is the "dedicated validation layer" the assignment grades (requirement
L20 / L90). It runs as a separate `ApplyGuardrail` call, independent of the LLM.

1. Bedrock (us-east-1) → **Guardrails** → **Create guardrail**.
2. **Name**: `census-chatbot-guardrail`.
3. **Denied topics** → Add topic:
   - Name: `Off-topic`
   - Definition: *"Any request not about US Census / American Community Survey
     demographic, population, housing, income, employment, or education data."*
   - Sample phrases: *"Write me a poem", "What's the weather", "Tell me a joke",
     "Help me write code", "Who won the game last night"*.
4. **Content filters**: leave the defaults on (Hate, Insults, Sexual, Violence,
   Misconduct, Prompt attack) at Medium/High.
5. (Optional) **Blocked messaging**: set the response shown when blocked, e.g.
   *"I can only help with questions about the US Census dataset."*
6. Create → then **Create version** (publish a numbered version, e.g. `1`).
7. Copy the **Guardrail ID** and the **version number**.

Put them in the backend env (version is the published number, e.g. `1` — NOT
"V1"; or `DRAFT` if unpublished):

```
BEDROCK_GUARDRAIL_ID=t40dhfwhl8at
BEDROCK_GUARDRAIL_VERSION=1
```

> The code fails *open* on guardrail infrastructure errors (logs a warning,
> lets the text through) but fails *closed* on a policy hit. So a
> misconfigured id degrades to "no filtering", not an outage — verify the id
> is set by checking the startup log line `guardrail=on`.

---

## 3. Create a least-privilege IAM user + access key

Render needs AWS credentials. Scope them to *only* Bedrock invoke + guardrail —
nothing else — so a leaked key from a third-party platform has minimal blast
radius.

1. Console → **IAM** → **Policies** → **Create policy** → JSON tab → paste
   [`bedrock-invoke-policy.json`](./bedrock-invoke-policy.json) (in this dir).
   Name it `census-bedrock-invoke`.
2. **IAM** → **Users** → **Create user** → name `census-render`.
   - Do **not** give console access.
   - Attach policy `census-bedrock-invoke`.
3. Open the user → **Security credentials** → **Create access key** →
   "Application running outside AWS" → copy the **Access key ID** and
   **Secret access key** (the secret is shown once).

These become Render env vars:

```
AWS_ACCESS_KEY_ID=<access key id>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=us-east-1
```

> boto3 (used by both BedrockModel and the guardrail client) reads these
> standard env vars automatically — no code change needed.

---

## 4. Render service env vars (full list)

When creating the Render service, set these (values from the steps above and
from `scripts/snowflake_readonly_role.sql`):

| Var | Value | Source |
|-----|-------|--------|
| `LLM_PROVIDER` | `bedrock` | default |
| `AWS_REGION` | `us-east-1` | step 1 |
| `AWS_ACCESS_KEY_ID` | … | step 3 |
| `AWS_SECRET_ACCESS_KEY` | … | step 3 |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | step 1 |
| `BEDROCK_GUARDRAIL_ID` | … | step 2 |
| `BEDROCK_GUARDRAIL_VERSION` | `1` | step 2 |
| `SNOWFLAKE_ACCOUNT` | `nhc33680.us-east-1` | existing |
| `SNOWFLAKE_USER` | `CENSUS_APP` | read-only role |
| `SNOWFLAKE_PASSWORD` | … | read-only role |
| `SNOWFLAKE_ROLE` | `CENSUS_READONLY` | read-only role |
| `SNOWFLAKE_DATABASE` | `CENSUS_DB` | existing |
| `SNOWFLAKE_SCHEMA` | `CENSUS_VIEWS` | existing |
| `SNOWFLAKE_WAREHOUSE` | `COMPUTE_WH` | existing |
| `API_KEY` | a strong secret (shared with reviewers) | `secrets.token_urlsafe(32)` |
| `CORS_ORIGINS` | the Render URL (same-origin, so optional) | after first deploy |

The Render build/start wiring (Dockerfile or render.yaml) is covered separately
once the AWS steps above are done.

---

## 5. Teardown (after the demo)

- Render: delete the service.
- AWS: deactivate/delete the `census-render` access key; optionally delete the
  IAM user and the guardrail.
- Snowflake: `DROP USER CENSUS_APP; DROP ROLE CENSUS_READONLY;` (optional).
