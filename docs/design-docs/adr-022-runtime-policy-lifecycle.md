# ADR-022: Versioned Runtime Policy Lifecycle

<!-- runtime-policy-contract: RuntimePolicyV1; schema=1; migration=m72_runtime_policy_snapshot; status=accepted -->

- Status: Accepted
- Date: 2026-09-05
- Decision owners: Moldy runtime and persistence boundaries

## Context

An agent is mutable configuration: its tools, model, middleware, and runtime
policy can change after a conversation starts. A conversation, however, must be
reproducible across retries, resumes, scheduled executions, and API calls. If a
resume silently picked up the agent's newest policy, the same checkpoint could
gain filesystem writes, lose Todo state, or summarize with a different context
budget.

M71 added nullable `agents.runtime_policy`. M72 added the immutable policy tuple
to `conversations` and provenance columns to `conversation_runs`. These two
migrations separate configuration for future conversations from execution
provenance for an existing conversation.

## Decision

### Canonical policy

`backend/app/agent_runtime/runtime_policy.py` defines the only accepted persisted
shape, `RuntimePolicyV1`. Pydantic strict mode rejects unknown fields and coerced
versions. The canonical JSON is UTF-8, key-sorted, compact JSON; its SHA-256 is
the policy hash. The persisted object fixes `version: 1` and has exactly three
configurable branches:

- filesystem mode: `inspect` or `artifact_write`;
- Todo projection: enabled or disabled;
- summarization: upstream `automatic` behavior (`mode: auto`) or the sole named
  preset `balanced_context_v1`.

The effective policy and its provenance are distinct. `stored` means the
snapshot came from an explicit agent policy, `legacy_compat` means a null or
already-executed legacy conversation uses the compatibility defaults, and
`server_owned` means a fixed server workflow such as Skill Builder owns the
policy.

### Conversation snapshot ownership

`backend/app/services/conversation_runtime_policy.py` locks the conversation and
agent rows before resolving the policy. The first writer stores the canonical
JSON, version, hash, and source on the conversation. Later executions parse and
verify that tuple instead of rereading mutable agent configuration. The tuple is
therefore immutable for the conversation's lifetime.

Normal durable `ConversationRun` records copy version, hash, and source from the
conversation for resume audit. Agent API and trigger services call the same
conversation snapshot resolver, although their separate run tables do not own
the copied tuple. Legacy prior-execution detection checks `ConversationRun`,
`AgentApiRun`, and `AgentTriggerRun` so an old conversation is not reinterpreted
under a newly edited policy.

Resume requires exact equality of version, hash, and source between the parent
`ConversationRun` and the conversation snapshot. A parent whose three fields
are all null may be backfilled only when the conversation source is
`legacy_compat` or `server_owned`. Partial nulls and every other mismatch fail
closed with `RUNTIME_POLICY_SNAPSHOT_INVALID`, which the run service exposes as
a conflict rather than starting or resuming execution.

### Capability enforcement

An explicit `stored` policy is hydrated only at the shared graph-build boundary:

- `inspect` exposes list/read/glob/grep; `artifact_write` additionally exposes
  write/edit, scoped to the conversation artifact tree. Delete and shell-like
  capabilities are not part of either stored-policy filesystem surface.
- Caller-supplied tools with reserved Deep Agents names are removed before the
  canonical filesystem, Todo, summarization, and subagent capabilities are
  installed.
- Filesystem permission lists are attested and reconstructed. Missing, copied,
  mutated, malformed, or over-broad attestations become an explicit deny-all
  rule because an absent Deep Agents permission list would otherwise allow.
- Todo middleware and the thread-state Todo projection both follow the
  snapshotted `todo.enabled` value.
- `auto` delegates parameter computation to Deep Agents 0.7.11.
  `balanced_context_v1` requires a positive model context-window capability and
  otherwise fails with `RUNTIME_SUMMARIZATION_POLICY_INVALID`.
- Parent-to-child filesystem propagation reconstructs permissions without
  broadening the parent's allowed operations or actor paths. Invalid trusted
  child names and invalid parent permission boundaries fail closed; a
  declarative child missing the identity required by a scoped backend is
  rejected before construction.
- Filesystem policy does not implicitly approve non-filesystem tools. Explicit
  `interrupt_on`/HiTL policy remains the authority for those tools.

The implementation boundaries are
`runtime_policy_capabilities.py`, `runtime_policy_parent_permissions.py`,
`runtime_summarization_policy.py`, `runtime_component_builder.py`, and
`conversation_agent_protocol_state_snapshot.py`.

### API, marketplace, and frontend boundaries

For runtime-policy data, agent create/update APIs accept only `runtime_policy`.
Responses may project the effective policy and source, but clients do not set
effective/source/hash or conversation snapshot metadata. Marketplace and Agent
Blueprint imports canonicalize the portable policy and reject reserved keys
such as `effective`, `source`, `hash`, `snapshot`, and the `runtime_policy_*`
provenance fields.

The agent creation and settings UI exposes filesystem, Todo, and summarization
choices and blocks a preset incompatible with the selected model context
window. `NEXT_PUBLIC_CHAT_RUNTIME` chooses the legacy or LangGraph v3/Deep
Agents runtime transport path only; it is not a policy input and cannot change a
conversation snapshot.

## Migration and rollback

- M71 introduces the nullable agent policy without changing legacy behavior.
- M72 backfills conversations with the compatibility policy and SHA-256. Skill
  Builder conversations use `server_owned`; other backfilled conversations use
  `legacy_compat`. Existing `conversation_runs` receive the owning
  conversation's provenance.
- Changing an agent policy is the rollback/forward mechanism for **new**
  conversations. Existing snapshots remain stable.
- Rolling back the policy schema or version is not a flag flip. It requires an
  explicit data migration and compatibility decision for persisted snapshots,
  hashes, checkpoints, and resumable runs.

## Security considerations

Snapshot validation is all-or-none and recomputes the canonical hash. Partial,
malformed, non-canonical, source-incompatible, or tampered tuples fail closed.
Permission construction trusts only fresh attested values, keeps protected
runtime trees denied, and terminates with a global deny. Child policies cannot
broaden parent permissions. Portable marketplace payloads cannot forge server
provenance. Errors expose stable codes rather than reflecting stored payloads.

## Testing strategy

The contract is covered at four layers:

1. strict parse, canonical JSON/hash, source, tamper, and null-tuple unit tests;
2. transactional first-writer-wins, prior-execution compatibility, copied run
   provenance, and resume mismatch service tests;
3. filesystem attestation, reserved-tool replacement, Todo projection,
   summarization hydration/error, and child-narrowing runtime tests;
4. agent API/UI and LangGraph v3 E2E scenarios for persisted choices, thread
   state, HITL, Todo behavior, and resume.

The documentation gates derive dependency versions and migration head from
tracked source, require this ADR's canonical contract marker, and require its
ADR index and documentation-inventory entries.

## Alternatives considered

### Resolve from the agent on every run

Rejected because editing an agent would retroactively change resumable
conversations and checkpoint capability boundaries.

### Snapshot only a hash

Rejected because execution would still need mutable external configuration to
reconstruct behavior, and a hash cannot by itself hydrate middleware.

### Let each client or run choose effective provenance

Rejected because untrusted callers could forge server ownership or broaden
capabilities. The server derives provenance and owns snapshot creation.

### Treat the frontend runtime flag as policy

Rejected because a deployment transport rollout must not mutate persisted
execution semantics.

## Consequences

Positive consequences are reproducible resumes, auditable provenance, explicit
capability boundaries, and safe agent-policy iteration for future
conversations. Costs are an additional locked read on first execution, policy
version migration obligations, and fail-closed errors when legacy or stored
metadata is inconsistent.

## Legacy retirement criteria

`legacy_compat` and the all-null parent-run exception may be removed only after
an observation period proves that every executable conversation has a valid
snapshot, every resumable `ConversationRun` has matching provenance, and no
Agent API or trigger history still depends on null-era interpretation. Removal
is a separate approved migration with rollback and full runtime/E2E gates; it is
not part of this ADR's current implementation.
