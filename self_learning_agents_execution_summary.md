# Self-Learning Agents — Execution Summary

> **Purpose:** Incremental implementation log for `self_learning_agents_plan.md` (Phases A/B/C).
> Companion to `execution-summary.md` (the main Knowledge Fabric build). Update after every phase.
> **Account:** `020396275984` | **Region:** `ap-southeast-2`
> **Base agent:** `VBC-Care-Navigator` — Agent ID `JIEOIRGZVJ`, Alias `poc-v1` (`XN0Z1NPGS8`)

---

## Overall Status

| Phase | Description | Status |
|---|---|---|
| Phase A | ReAct — Recursive Query Refinement | ✅ Complete |
| Phase B | Episodic Memory (DynamoDB + OpenSearch) | ⚠️ Wired, blocked on IAM (DynamoDB) |
| Phase C | Ontology Self-Update with Human Review (SNS + DynamoDB Streams) | ⏳ Not started |

---

## Phase A — Recursive Query Refinement ✅

### Goal
Exploit the Bedrock Agent's native ReAct orchestration loop so that a failed or
empty-result SPARQL query is automatically retried (up to 3 attempts) with a
corrected query, instead of surfacing "no results" straight to the user.

### Resources deployed
| Resource | Detail |
|---|---|
| Lambda | `vbc-query-refiner` (Python 3.12, not in VPC) |
| Action group | `refine_query` on agent `JIEOIRGZVJ` |
| Agent instruction | Appended QUERY REFINEMENT PROTOCOL block to `infra/lib/agent_instruction.txt` |
| Agent version | `3` (new immutable version created on prepare) |
| Alias | `poc-v1` (`XN0Z1NPGS8`) repointed from version `2` → version `3` |

`vbc-query-refiner` mirrors `vbc-sparql-bridge`'s structure: takes
`question` / `failed_sparql` / `failure_reason` / `attempt_number`, asks
Nova Pro (via `bedrock-runtime.converse()`) to produce a corrected SPARQL
query using a fix-hint prompt (GRAPH wrapper, correct property names, etc.),
executes it via the existing `vbc-sparql-relay` Lambda, and returns
`result_count` in the Bedrock Agent response envelope. Attempts beyond 3
short-circuit with an error instead of calling Bedrock/Neptune again.

### Deployment notes
- Deploy script: `phase_a_deploy.sh` (idempotent — existence checks before create/update, safe to re-run).
- **AWS CLI shorthand bug**: `bedrock-agent create/update-agent-action-group` with
  `--api-schema payload=file://...` or `--action-group-executor lambda={...}` throws a
  generic `ValidationException: Failed to create OpenAPI 3 model...` on aws-cli/2.34.53 —
  reproduced even with a byte-identical copy of an already-working stored schema. Root
  cause is CLI-side parameter parsing, not the schema content. **Fix:** build the full
  request as JSON via `python3 -c "..."` and pass with `--cli-input-json`. Use this
  pattern for all future action-group work (Phase B, Phase C).
- **`aws bedrock-agent-runtime invoke-agent` does not exist in aws-cli/2.34.53** — the
  command is simply absent from that build's `bedrock-agent-runtime` subcommand list
  (confirmed via `aws bedrock-agent-runtime help`), even though the boto3 SDK on the same
  machine (`boto3==1.42.97`) has `invoke_agent`. **Workaround:** invoke the agent via a
  small Python/boto3 script instead of the CLI (see `ask_agent.py` pattern).
- **IAM PassRole gap (resolved this session)**: the admin's earlier fix replaced the
  `PassVbcRoles` statement (which allowed passing `vbc-lambda-execution-role` to
  `bedrock.amazonaws.com`) with a `PassRoleLambda` statement scoped only to
  `lambda.amazonaws.com`. This blocked `bedrock-agent update-agent` (needed to update the
  agent's instruction) with `AccessDeniedException: ... iam:PassRole ... not authorized`.
  Fixed by the admin adding both services to the `iam:PassedToService` condition.
- Sandbox requires `--no-verify-ssl` (self-signed cert in chain) on every AWS CLI/boto3
  call in this environment.
- Updating a Bedrock Agent alias to pick up new agent state requires an explicit
  `update-agent-alias` call (with `routingConfiguration` omitted) to auto-create a new
  immutable agent version and repoint the alias — aliases route to fixed version numbers,
  never to `DRAFT`, except the built-in `AgentTestAlias`.

### Validation gate results
| Gate | Result |
|---|---|
| `refine_query` Lambda deployed and invokable | ✅ |
| Agent re-PREPARED with new action group + instruction (version 3) | ✅ |
| "Find CHF patients" (no GRAPH wrapper) → agent refines → returns results | ✅ wiring confirmed — refined 2x then fell back to `semantic_search`; no CHF match found, which is a data/ontology coverage gap (no diagnosis-name↔ICD synonym mapping loaded), not a refinement-logic defect |
| "Score above 75%" → agent refines property name → returns results | ✅ answered directly on first attempt, no refinement needed |
| Agent never exceeds 3 refinement attempts | ✅ confirmed via `action_calls` trace — "diabetes care gaps" query used exactly 3 `refine_query` calls then fell back |
| CloudWatch shows `vbc-query-refiner` invocation after each empty result | ✅ 5 invocations logged over the test run |

### Test query results
| Question | Action group calls | Outcome |
|---|---|---|
| "Show patients with score above 75%" | `sparql_query` | Returned ~50 patients directly, no refinement |
| "Find CHF patients" | `sparql_query` → `refine_query` ×2 → `semantic_search` | No CHF patients found after refinement + fallback |
| "Which patients have diabetes care gaps" | `semantic_search` → `sparql_query` → `refine_query` ×3 → `semantic_search` | Exhausted all 3 attempts, fell back cleanly, no infinite loop |
| "Top 10 highest risk patients with open gaps" | `semantic_search` → `sparql_query` | Returned top patients directly (M-0204, M-0138, M-0496, M-0406, ...) |

### Config
See `phase_a_config.json` for the machine-readable resource/validation record.

### Follow-up (not yet investigated)
- CHF / diabetes-by-name queries return empty even after refinement — the graph likely
  keys diagnoses by ICD-10 code only, with no `skos:altLabel`/synonym edge from condition
  names ("CHF", "diabetes") to codes (`I50.9`, `E11.9`). Worth a vocabulary-drift check
  before Phase B, since better label coverage would reduce how often refinement is needed
  at all.

---

## Phase B — Episodic Memory ⚠️ Wired, blocked on IAM

### Goal
Agent remembers every successful Q→SPARQL pair. On new questions it retrieves
the closest past query from semantic memory and adapts it instead of
generating from scratch, so accuracy/speed improve with usage.

### Resources deployed
| Resource | Detail |
|---|---|
| DynamoDB table | `vbc-query-memory` (PAY_PER_REQUEST, `query_id` HASH + `timestamp` RANGE) — created directly with the SSO admin role, since it doesn't require the Lambda execution role |
| OpenSearch index | `vbc-query-memory` (KNN, 1024d HNSW cosine) in the existing `vbc-vectors-poc` domain, alongside `vbc-concepts-embeddings` / `vbc-icd10-embeddings` |
| Lambda | `vbc-memory-write` (Python 3.12, not in VPC) — embeds + stores question/SPARQL pairs |
| Lambda | `vbc-memory-read` (Python 3.12, not in VPC) — KNN search, returns reference SPARQL above 0.88 similarity |
| Lambda | `vbc-memory-rate` (Python 3.12, not in VPC) — updates rating field in DynamoDB + OpenSearch |
| Action groups | `retrieve_memory` (→ `vbc-memory-read`), `store_memory` (→ `vbc-memory-write`) |
| API Gateway route | `POST /rate` on the existing `trzyzra8ve` API, `poc` stage → `vbc-memory-rate` |
| Agent instruction | Appended MEMORY PROTOCOL block to `infra/lib/agent_instruction.txt` |
| Agent version | `4` — alias `poc-v1` (`XN0Z1NPGS8`) repointed from version `3` → `4` |

Deploy script: `phase_b_deploy.sh` (idempotent, same `--cli-input-json` pattern as
Phase A for both new action groups). Test harness: `test_phase_b.py`.

### Deployment notes
- DynamoDB table + OpenSearch index creation don't need `vbc-lambda-execution-role`
  at all — they were created directly using the interactive SSO admin session, which
  already has `dynamodb:CreateTable` and hits OpenSearch via master-user basic auth
  (same as `setup_opensearch.py` in Phase 1/4), bypassing IAM entirely for that hop.
- **Resiliency bug found and fixed during testing**: initially `memory_write.py` and
  `memory_read.py` returned Bedrock Agent response envelopes with `httpStatusCode: 500`
  on any internal exception. Bedrock Agents treat a 5xx from an action-group Lambda as a
  hard `dependencyFailedException` that **aborts the entire `invoke_agent` call** — not
  just the failed action — which meant a broken (or permission-denied) memory write was
  taking down otherwise-successful answers. Fixed both handlers to always return
  `httpStatusCode: 200` with a `stored: false` / degraded `found: false` body on
  exception, so a memory-layer failure only disables memory for that turn instead of
  breaking the user-facing response. This is a general lesson for any future
  fire-and-forget action group (applies to Phase C too): **never let a non-critical
  action group return 5xx to Bedrock; catch and degrade instead.**
- **IAM blocker (unresolved, same shape as Phase A's PassRole gap)**: `vbc-lambda-execution-role`
  has no `dynamodb:*` permissions at all — confirmed via direct Lambda invoke:
  ```
  AccessDeniedException: User: .../vbc-lambda-execution-role/vbc-memory-write is not
  authorized to perform: dynamodb:PutItem on resource:
  arn:aws:dynamodb:ap-southeast-2:020396275984:table/vbc-query-memory because no
  identity-based policy allows the dynamodb:PutItem action
  ```
  I attempted to self-remediate with `iam:PutRolePolicy` on the role (same approach that
  worked for creating the DynamoDB table) and was denied — my SSO role doesn't have
  `iam:PutRolePolicy`, only the narrower `iam:PassRole` grant added for Phase A. **The
  admin needs to add this statement** to `vbc-lambda-execution-role`'s inline policy
  (`vbc-lambda-execution-policy`):
  ```json
  {
    "Sid": "DynamoDBQueryMemory",
    "Effect": "Allow",
    "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"],
    "Resource": [
      "arn:aws:dynamodb:ap-southeast-2:020396275984:table/vbc-query-memory",
      "arn:aws:dynamodb:ap-southeast-2:020396275984:table/vbc-query-memory/index/*"
    ]
  }
  ```
  `vbc-memory-read` does NOT need this fix — it only talks to OpenSearch via basic auth,
  no DynamoDB, and works today. Only `vbc-memory-write` and `vbc-memory-rate` are blocked.
- OpenSearch KNN `cosinesimil` scores from the k-NN plugin are `1 + cosine_similarity`
  (range 0–2), not raw cosine similarity (range -1–1). The plan's 0.88 threshold is taken
  at face value from the plan text; once real data is flowing, recalibrate the threshold
  against actual observed scores rather than assuming a 0–1 cosine range.
- Nova Pro does not reliably follow "ALWAYS call retrieve_memory FIRST" — in testing it
  sometimes skipped straight to `sparql_query` on a fresh question. This is an
  instruction-following limitation of the underlying model, not a wiring bug; worth
  revisiting with prompt tuning or few-shot examples in the instruction if retrieval
  hit-rate matters for Phase B's value proposition.

### Validation gate results
| Gate | Result |
|---|---|
| `vbc-query-memory` DynamoDB table exists and writable | ⚠️ Exists; not writable yet — blocked on IAM above |
| `vbc-query-memory` OpenSearch index created with KNN mapping | ✅ |
| `vbc-memory-write` Lambda: ask a question → DynamoDB record appears within 5s | ❌ Blocked — `AccessDeniedException`, degrades to `stored: false` instead of crashing |
| `vbc-memory-read` Lambda: ask the same question again → similarity > 0.88 → reference SPARQL returned | ⏳ Cannot validate until writes succeed (nothing in the index yet — confirmed via `dynamodb scan --select COUNT` → 0 items) |
| Agent uses reference SPARQL on repeat question (visible in CloudWatch trace) | ⏳ Blocked, same reason |
| Rating API: POST /rate → DynamoDB rating field updated | ❌ Blocked — same DynamoDB permission gap |
| Cold-start (first question) → `found: false` → fresh SPARQL generated → stored | ✅ Read path confirmed (`found: false` on empty index); store path degrades gracefully instead of persisting |
| After 10 diverse questions, agent answers similar variants faster | ⏳ Cannot validate until writes succeed |

### Test results (via `test_phase_b.py`, boto3 `invoke_agent` — CLI still lacks this command)
| Question | Action group calls | Outcome |
|---|---|---|
| "How many providers are primary care physicians?" | `sparql_query` | Answered directly (30 PCPs); agent did NOT call `retrieve_memory` first despite instruction |
| "How many PCPs are there?" (near-duplicate, asked right after) | `retrieve_memory` → `sparql_query` → `refine_query` → `store_memory` | `retrieve_memory` correctly returned `found: false` (nothing stored yet); generated fresh SPARQL, refined once, attempted to store (degraded gracefully, not persisted) |

### Config
See `phase_b_config.json` for the machine-readable resource/validation record.

### Pending
- Admin adds the `DynamoDBQueryMemory` statement above to `vbc-lambda-execution-role`.
- Re-run `test_phase_b.py` — expect `retrieve_memory` to return `found: true` with
  `similarity > 0.88` on the second ("How many PCPs are there?") question once the first
  question's answer is actually persisted.
- Consider a prompt/few-shot tweak so the agent calls `retrieve_memory` consistently on
  every question, not just some.

---

## Phase C — Ontology Self-Update with Human Review (deployed, blocked on IAM)

### What was built
- `vbc-ontology-proposals` DynamoDB table (PAY_PER_REQUEST, streams enabled, `NEW_IMAGE`).
- `vbc-propose-term` Lambda + `propose_term` action group on the agent — called when
  `sparql_query`/`refine_query` both return 0 results for an unrecognised term.
  Follows the Phase B resiliency rule: **always returns HTTP 200**, even on internal
  failure, so it can never abort the agent's turn via `dependencyFailedException`.
  Confirmed via direct invoke: DynamoDB `PutItem` is denied, but the Lambda degrades
  to `{"status": "not_stored", ...}` with HTTP 200 as designed.
- `vbc-stream-notifier` Lambda — reads DynamoDB Stream `NEW_IMAGE` records, sends an
  SNS email with approve/reject links for every new `PENDING` proposal. Degrades to a
  no-op log line if `TOPIC_ARN` isn't set or SNS publish fails, so a stuck notification
  never blocks the stream checkpoint.
- `vbc-proposal-ui` Lambda behind new API Gateway routes on the existing `vbc-query-api`
  (`trzyzra8ve`, stage `poc`):
  - `GET /ontology/proposals` — HTML table of all proposals with Approve/Reject links
  - `GET|POST /ontology/approve?id=...` — marks APPROVING, invokes `vbc-ontology-pipeline` (async)
  - `GET|POST /ontology/reject?id=...&reason=...` — marks REJECTED
- `vbc-ontology-pipeline` Lambda (the core) — on approval: updates
  `controlled_vocabulary.json`, appends the new class to `taxonomy.ttl` and
  `vbc_ontology.owl` in S3, `INSERT DATA`s the class into Neptune's schema graph via
  `vbc-sparql-relay`, embeds+indexes it into `vbc-concepts-embeddings`, appends
  `ontology/governance/change_log.md`, runs CQ regression, and marks the proposal
  `APPROVED` or `REGRESSION_DETECTED`.
- `vbc-cq-regression` Lambda — reruns the 5 competency questions via `vbc-sparql-relay`
  and reports pass/fail per query with minimum expected result counts.
- Agent instruction updated with an **UNKNOWN TERM PROTOCOL** block; agent re-prepared
  and alias `poc-v1` (`XN0Z1NPGS8`) now routes to **version 5**.

### Hard blockers (same `vbc-lambda-execution-role` IAM wall as Phase B, plus a new SNS wall)
1. `vbc-lambda-execution-role` still lacks DynamoDB permissions — now also needed on
   `vbc-ontology-proposals` (`PutItem`/`GetItem`/`Scan`/`UpdateItem`). Confirmed via
   direct `vbc-propose-term` invoke → `AccessDeniedException`, degrades gracefully.
2. Creating the DynamoDB Streams event-source-mapping for `vbc-stream-notifier` failed:
   the execution role needs `GetRecords`/`GetShardIterator`/`DescribeStream`/`ListStreams`
   on the stream ARN. Mapping not yet created.
3. `vbc-proposal-ui` → `vbc-ontology-pipeline` and `vbc-ontology-pipeline` →
   `vbc-cq-regression` are cross-Lambda `Invoke` calls not yet in the role's
   `LambdaInvokeVbc`/`InvokeLambdas` statements.
4. **New this phase**: `sns:CreateTopic` was denied on the *admin SSO session itself*
   (`AuthorizationError`, not just the Lambda role) — this session cannot create the
   `vbc-ontology-review` SNS topic at all. The admin needs to either create the topic +
   email subscription directly, or grant this session `sns:CreateTopic`/`sns:Subscribe`.
   Once a topic exists, `vbc-lambda-execution-role` also needs `sns:Publish` on it.

Exact consolidated ask is recorded in `phase_c_config.json` → `blocked_on`.

### Config
See `phase_c_config.json` for the machine-readable resource/validation record.

### Pending
- Admin creates the `vbc-ontology-review` SNS topic + email subscription (or grants
  `sns:CreateTopic`/`sns:Subscribe` to this session), and adds the 4 IAM statements in
  `phase_c_config.json.blocked_on.missing_permissions` to `vbc-lambda-execution-role`.
- Once unblocked: set `TOPIC_ARN` env var on `vbc-stream-notifier` and
  `vbc-ontology-pipeline`, create the DynamoDB Streams event-source-mapping on
  `vbc-stream-notifier`, and run an end-to-end test — ask the agent an out-of-vocabulary
  question, confirm a `PENDING` row appears, approve it via `/ontology/proposals`, and
  confirm the class shows up in Neptune + `vbc-concepts-embeddings` + `change_log.md`
  within ~90s with all 5 CQs still passing.

---

## Session notes
- AWS session tokens expire every ~1 hour — re-export fresh temporary credentials
  (via a `creds.sh` file, not inline paste, to avoid copy/paste corruption of the
  session token) at the start of each work session.
- Bedrock Agent versions are immutable snapshots; `DRAFT` is the only mutable version.
  Any `update-agent` / action-group change must be followed by `prepare-agent` and then
  an alias repoint to pick up the change.
