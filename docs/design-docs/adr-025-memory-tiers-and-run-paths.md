# ADR-025: Memory in Three Tiers, Governed by the Run Path

- Status: Accepted (2026-11-07)
- Date: 2026-11-07
- Decision owners: Moldy runtime, memory and authorization boundaries
- Source: Moldy feature plan v2, item 15 (C6); `docs/exec-plans/active/memory-policy-plan.md`
  (decisions Q1–Q14); PRs #368, #370, #371, #372, #373 and PR-B3

## Context

Before item 15, every active memory of a person went into the system prompt of every
run, whatever started the run: chat, a schedule, the Agent API or a child agent. A
memory saved from a web page could then steer later runs, the prompt grew with the
number of memories, a delete left copies in proposals and history, and a schedule or an
external caller read the owner's personal memories.

## Decision

### 1. The run path decides what a run may read and write

`AgentConfig.run_path` (chat, trigger, api, channel, subagent) is set by each entry and,
for a resume, taken from the first run of the resume chain (`memory_paths.origin_run_path`;
a broken chain reads as `channel`). `memory_paths.memory_access` maps it to the scopes a
run may read and whether it gets write tools:

| Path | Personal memory | This agent's memory | Write tools |
|---|---|---|---|
| chat | yes | yes | yes |
| trigger | no | yes | yes (always proposals) |
| api, channel, subagent | no | no | no |

The person's settings and the agent's override can only narrow this.

### 2. Three tiers, each within a budget

1. **Profile documents** in the system prompt: "내 프로필" (every agent) and the
   per-agent "작업 메모", 800 tokens each, one sourced line per fact
   (`memory_profile`). A line is read only while all its sources are active memories of
   the person.
2. **Automatic recall** as a one-off message just before the latest user message, on the
   root agent only (`MemoryRecallMiddleware`; never the checkpoint, the stored events or
   a child agent). Raw-score floors per list (keyword `termShare`, cosine per measured
   `model@dimensions`), RRF ordering, per-scope caps and a 1,500-token budget
   (`memory_recall`). Nothing passing the floors means nothing goes in. Recall is
   computed once per resume chain and recorded without text.
3. **`search_memory`** for the rest, with the same floors; results are framed as
   untrusted data and masked on stored surfaces
   (`protocol_redaction.MEMORY_RESULT_TOOL_NAMES`).

Memories are never listed in the system prompt.

### 3. One write path, and the server decides the origin

Every memory write goes through `memory_writes.write_memory`: checks (high-risk personal
data and pseudonym tokens refused; sensitive, contact, instruction-like and other-person
text marked), erased-text refusal, exact duplicates, then the decision of
`memory_policy.decide_record_write` (approval mode × run path × origin). Only text a
person typed on the memory screens is `user_stated`. A model tool call is
`agent_inferred`, or `tool_derived` once outside content reached the conversation.

### 4. Consolidation writes no new memory sentence

The background job (`memory_consolidation`) gets ids and actions from the system model
and builds every result itself: a merge is the originals joined word for word; a profile
line must carry its sources and contain only numbers, dates and Latin names found in
them. Replacing or removing a person-confirmed memory, a contradiction and a mismatch
with the source message are always proposals and never bulk-approved.

### 5. Company and personal tenants differ only in defaults

The company memory policy (`tenants.settings["memory"]`) has the same keys for both kinds
(`memory_policy.memory_policy_defaults`). The memory vector model is a registered
embedding model (`models.kind = 'embedding'`) chosen there, or none.

### 6. A delete is real

`memory_lifecycle.purge_records` removes the row, its revisions, sources, vectors and
recall records, clears the text of proposals made from it, removes profile lines citing
it and stores a keyed digest so an agent cannot write the same text again. The receipt
names what it cannot reach (conversation history, backups).

## Consequences

- A new path, a new memory tool, a new memory write or a new recall surface follows the
  rules above; `CLAUDE.md` (Backend) lists the enforced ones.
- Vector recall stays off until a model's floor is measured on the eval set
  (`backend/evals/memory_recall`): the first measurement did not meet the pre-registered
  "nothing recalled" criterion.
- Open: `search_memory` in prepared lease runs (needs a snapshot-safe tool), the trigger
  memo and its end-of-run "최근 상태" proposal (PR-B3b).
