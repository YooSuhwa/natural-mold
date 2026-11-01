# ADR-024: Common Execution Contract for New Features

- Status: Accepted (2026-11-01; gaps listed under Consequences are open)
- Date: 2026-11-01
- Decision owners: Moldy runtime, authorization and persistence boundaries
- Source: Moldy feature plan v2 (2026-10-27), section 4.3 and release unit 0A

## Context

The feature plan adds 25 features: knowledge search, DB and OpenAPI connections,
an MCP gateway, model kinds, attachments to the model, guardrails, an approval
inbox, workflows, and others. Each one touches the same questions: who the run
acts as, which company it belongs to, which versions it uses, what it costs,
what happens to its files, and what a retry does. If every feature answers these
on its own, they will drift.

Several answers already exist in code but are not written down as a contract,
and some are missing. This ADR records what is guaranteed today and the rule new
features follow. It does not change runtime behavior by itself. Each rule names
the test that proves it, so a feature PR can show it complies.

Paths are relative to `backend/app/`.

## Decision

### 1. Execution paths

**Today.** Two paths exist. The default path builds the agent in
`_prepare_agent`. The prepared lease path (`PREPARED_RUN_LEASE_ENABLED`, off by
default, `config.py`) compiles the root and child graphs once and freezes the
binding, snapshots, tool access and secrets in a process-local lease
(`agent_runtime/runtime_config.py`). Only a digest is persisted
(`models/conversation_run.py`, `runtime_materialized_at` + sha256). Shapes the
lease cannot carry fall back to the default path for a listed reason
(`agent_runtime/prepared_run_input_policy.py`, `LEGACY_FALLBACK_REASONS`).

Entry points: chat (send, edit, regenerate, resume, `run.start`, `input.respond`,
fork) and the input queue reach `_run_conversation`; the Agent API
(`agent_api/run_execution.py`) and triggers (`agent_runtime/trigger_execution_invoke.py`)
have their own launchers; subagents are built in `agent_runtime/subagents.py`
or inside the lease.

**Rule.**
- A feature PR states which paths it supports. Supporting only the default path
  is allowed, but the lease path must then fall back with a new
  `LEGACY_FALLBACK_REASONS` entry, never fail silently or run without the
  feature.
- A feature that changes what the model or a tool sees is tested on both paths
  or on the default path plus the fallback.
- Every entry point listed above is either supported or refused with an explicit
  error. A feature does not apply to chat only by accident.

### 2. Identity and company isolation

**Today.** `accept_owned_execution` (`services/agent_execution_acceptance.py`)
writes `org_id`, `tenant_id`, `run_source`, `invoker_type`, owner,
`on_behalf_of` and `credential_subject` onto the run and each participant
(`tenancy/attribution.py`). `per_user` acts as the chat caller; `fixed`,
triggers and API calls act as the owner. The credential itself is resolved at
prepare time (`agent_runtime/credential_resolution.py`): agent credential, then
model default, then the subject's own matching credential, then a company shared
key designated to the organization. There is never a fallback to a system key.

**Rule.**
- Identity is fixed at accept time from the run row, never re-derived from the
  request or from model arguments. Folder ids, connection ids, user identity and
  row filters are filled by the server (feature plan 4.1).
- The credential subject is fixed at accept time. The concrete key may still be
  resolved at prepare time, but only within that subject and company. A feature
  that adds a credential use records the scope (`personal` or `tenant`) the way
  `credential_resolution` does.
- Tests: the same user in two organizations, the same file hash in two
  companies, `fixed` and `per_user`, a company shared key, and each entry point
  (chat, API, trigger, subagent).

### 3. Revocation timing

**Today.** Every execution entry goes through the canonical gateway
(`authz/organization_execution_gateway.py`), which locks tenant, organization,
memberships, user, agent and conversation, and fails closed on a suspended
company, an inactive user or a removed member. Queue launch re-authorizes the
target and the frozen resource context (`services/conversation_run_queue_launch.py`).
Under a lease, a fresh permit is taken before each provider call, tool call and
child launch (`agent_runtime/lease_dispatch_permit.py`). The permit checks
company, membership and user, not resource grants. The default path has no check
during execution. `organizations.authz_epoch` is bumped on revocation but is not
read by the runtime.

**Rule.**
- A revocation takes effect at the next boundary: queue launch, run start,
  resume after approval, and (lease path) the next provider or tool call. A call
  already in flight is not interrupted.
- A new resource type (knowledge folder, DB connection, OpenAPI server) is
  re-checked at tool-call time, not only at materialization, because the lease
  permit does not cover resource grants. Search caches and citations are
  filtered by the same check.
- An approval resume re-checks identity, permission, expiry and the original
  arguments before running (feature plan C5).
- Tests: revoke after queue accept, while waiting for approval, and just before
  execution; suspend the organization before execution.

### 4. Version pinning

**Today.** The runtime policy is pinned per conversation and copied to each run
(ADR-022; `services/conversation_runtime_policy.py`). Resume requires an exact
match. Model, tools and skills are not pinned: they are read from the live agent
row at each run start, including queued runs and resume.

**Rule.**
- Pinned per conversation (ADR-022): runtime policy.
- Pinned per accepted input: anything the user chose or attached for that input
  (selected model in B2, attachment version and input form in B3, guardrail
  policy version in C4). The value is stored with the input
  (`ConversationRunInput` or the run row) at accept time and re-validated, not
  re-chosen, at execution.
- Live at run start (unchanged): agent model, tools and skills, until a feature
  needs them pinned. A feature that pins one of them adds a column and a
  resume/fork rule in the same PR.
- Tests: change the setting after accept, then run, resume and fork; the stored
  value is used.

### 5. External side effects

**Today.** Lease provider calls retry three times with a new permit each time;
SDK retries are off (`agent_runtime/lease_provider_retry_policy.py`).
`ToolRetryMiddleware` is banned under a lease. MCP tools make a single attempt.
Risk levels decide approval and block write tools in triggers (`tools/risk.py`).
There are no idempotency keys and no unknown-outcome state for tool calls.

**Rule.**
- Tool calls that change external state (write, send, delete) are never retried
  automatically. Read calls may be.
- A connection feature declares an idempotency class per operation: `none`,
  `key` (accepts an idempotency key Moldy derives from run id and tool call id),
  or `read`. Destructive `none` operations require approval in unattended runs.
- When a process stops after sending and before storing the result, the outcome
  is recorded as `unknown`, distinct from success and failure, with a manual
  check path. This is new and lands with the first connection that writes
  (Spore change operations, feature plan A3).
- Tests: stop the process before sending, after sending, and before storing the
  response.

### 6. File lifecycle

**Today.** Company files live under `tenants/<tenant_id>/`; reads are checked
against the row's company (`storage/paths.py`). Limits are per file and per run
(`config.py`): upload 20 MiB, artifact 100 MiB, 100 files per run. There is no
storage quota and no trash for conversations or artifacts. S3 is not
implemented. Orphan uploads are collected by GC; retention cleanup is best
effort after the delete commits (`services/retention/conversations.py`).

**Rule.**
- New stored files use `tenant_relative()` paths. Every cleanup path that
  deletes them resolves the same function, and has a test that leaves zero files
  (PR #329 fixed one case that did not).
- Database rows commit first, file deletion follows, and a failed deletion
  leaves an orphan for GC rather than undoing the row delete.
- Quotas, trash and per-file retention are defined in B3 before they ship; until
  then no feature adds its own quota rule.

### 7. Usage accounting

**Today.** `token_usages` has one row per run, unique on `run_id`
(`services/chat/usage.py`). Subagent tokens are summed into the parent run's
metrics by namespace (`agent_runtime/run_metrics.py`). A HITL resume is a new run
with `parent_run_id`, and a pre-input baseline prevents double counting
(`agent_runtime/run_metrics_baseline.py`). `daily_spend_*` is written through an
in-memory batch queue that can drop rows on failure (`services/spend_writer.py`).
The requester's scope pays (`hooks/builtin/spend_hook.py`).

**Rule.**
- Counting units: logical request (one user input), run (one execution
  attempt), model call, and child call. Each total is derived from one of these
  without double counting. `parent_run_id` is not used to decide whether a run
  is a child (feature plan F1); a resume is a run, not a child call.
- Non-token units (embedding batches, rerank documents, audio seconds, images)
  are recorded as unit usage events, not converted to tokens.
- Background work (indexing, test calls) names its payer when it is designed:
  the folder or connection owner's scope, never the platform.
- Tests: a request with an approval resume and a subagent call has no duplicate
  in any total.

### 8. Migration and rollback

**Today.** Schema changes use expand, backfill, then contract (m81 to m83), with
real downgrades. Behavior changes ship behind flags with a legacy fallback.

**Rule.**
- Same pattern for new tables and columns. A migration that cannot be reversed
  says so in its docstring and in the PR.
- Turning a flag off must leave existing rows readable and runs recoverable.
  Known gap to close before relying on it: with `PREPARED_RUN_LEASE_ENABLED`
  off, the prepared-run orphan sweep is skipped
  (`services/conversation_run_prepared_recovery.py`).
- Tests: stop a backfill midway and rerun it; roll back and replay existing
  conversations.

### 9. External API compatibility

**Today.** The Agent API has only `/v1` with Dify- and OpenAI-style shims
(`routers/agent_runtime_api.py`). `AgentRunRequest` accepts unknown fields
(`schemas/agent_api.py`); the OpenAI shim ignores everything except model,
messages and stream. There is no request idempotency outside chat queue inputs
(`client_request_id`). Deployment `daily_token_limit` and
`rate_limit_per_minute` are stored but not enforced.

**Rule.**
- A new external contract (OpenAI Agents API in E2, messenger webhooks in E4)
  pins the spec and SDK version it targets, publishes the supported subset, and
  rejects unsupported options with an explicit error instead of ignoring them.
- Requests that create work accept an idempotency key; a repeat with the same
  key returns the original result.
- Existing `/v1` behavior is kept as is. Tightening it (rejecting unknown
  fields, enforcing the stored limits) is a separate, announced change.

## Consequences

- Each feature PR includes a short "execution contract" section listing the nine
  items: supported paths, identity rule, revocation boundary, pinned values,
  side-effect class, file paths, usage units, migration, and external contract.
  "Not applicable" is a valid answer with a reason.
- Gaps recorded above are owned by the feature that first needs them: resource
  re-check at tool time (A1, A2, A3), `unknown` outcome (A3), unit usage events
  (B1), quotas and trash (B3), idempotency keys on the API (E2), and the
  prepared-run sweep with the flag off (before the lease becomes the default).
- Test names "all E2E pass" are not evidence by themselves. Results record
  which of scripted, live provider, native PostgreSQL with RLS, external OAuth
  and browser runs were executed.

## Related

- ADR-002 checkpointer, ADR-011 stream resume, ADR-012 HITL middleware,
  ADR-013 service LLM keys, ADR-016 multi-user auth, ADR-018 relative storage
  paths, ADR-020 run event adapter, ADR-021 trace redaction, ADR-022 runtime
  policy lifecycle, ADR-023 organization authorization.
