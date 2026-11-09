# ADR-026: Workflow Execution on Step Rows, in the Job Worker

- Status: Accepted (2026-11-09)
- Date: 2026-11-09
- Decision owners: Moldy runtime, approvals and authorization boundaries
- Source: Moldy feature plan v2, item 23 (22.9 "워크플로 1차 핵심");
  `docs/exec-plans/active/workflow-plan.md` (decisions Q1–Q9); PRs #384–#395 and W4b

## Context

A workflow chains agents the person already built in a fixed order: the outer flow (order,
what each step receives, branches, approvals, failure) belongs to the workflow, and inside an
agent step the agent runs with its own tools and policies. This differs from subagents,
where the model chooses to delegate while it runs. The first version needed seven node kinds
(start, agent, rule decision, approval, transform, output, fail), a run that survives a
worker restart without running an agent step twice, and approvals that wait for days.

## Decision

### 1. A versioned definition with structured references

A workflow has one draft (`draft_definition`, `draft_revision`; a stale save is 409) and
immutable published versions (`workflow_versions`). The definition is
`moldy.workflow.v1` (`app/workflows/definition.py`): templates are lists of segments
(`{"text"}` or `{"ref": {"node", "path"}}`), never expressions, so renaming a node cannot
break a reference. Publishing runs the server checks (`app/workflows/checks.py`): one start,
an end, no cycles, every branch exit wired, approvals with a time limit, references only to
nodes that come before on **every** path (`ref_not_upstream`), agents that exist, are not
archived, are runnable by the owner and have a model with tool calling. Values passed to an
agent are wrapped as data, not instructions (`app/workflows/values.py`).

### 2. Agents are pinned by fingerprint (Q1 C)

Publishing stores, per agent node, the agent id and a hash of every agent column and link
row that changes how it runs (`app/workflows/pins.py`; a test fails when a new table that
cascades from `agents` is not classified as pinned or excluded). A run starts only when the
live agents still match (`WORKFLOW_AGENT_CHANGED` otherwise: publish again). Each agent step
also checks, as it starts, that the agent is still there, not archived and runnable by the
runner (`agent_node_issues`); a failed check fails the step with that code.

### 3. The run is a list of step rows, advanced by the `workflow.run` job

`POST /api/workflows/{id}/runs` stores a queued `workflow_runs` row and a `workflow.run` job.
The job runs in the job worker (`python -m app.jobs.worker`, Compose `worker`), never in the
API process, so the worker starts the checkpointer and the default hooks itself
(`app/jobs/worker.serve`). The record of progress is the `workflow_step_runs` rows: with no
loops or parallel branches in the first version a run is a single line, so the last finished
step and the branch it chose decide the next node. The outer flow is not a LangGraph graph
and has no checkpoint of its own; a reclaimed job reads the step rows and continues.

An agent step is the envelope of one agent execution: an internal conversation
(`source="workflow"`, hidden from every conversation listing), acceptance through
`accept_owned_execution(source="workflow")`, invoke mode, `run_path="workflow"`, the node's
`output_schema` as structured output, and `run_source=WORKFLOW` for usage. A second
acceptance of the same step is refused by the envelope's `conversation_run_id IS NULL` check.
Workflow steps read the agent's memory and get no memory write tools (Q4,
`memory_paths._ACCESS["workflow"]`).

### 4. Three kinds of waiting, one inbox

| Stop | Request | Decision |
|---|---|---|
| Approval node | `approval_requests.kind="business"`, no conversation or run id, points at the step; time limit required, expiry takes the rejected exit | approve/reject exit, then a new `workflow.run` job |
| Tool approval inside an agent step | the agent's standard interrupt, `RunOrigin.workflow_*` | the chat resume path; `tool_waits.adapt_resume` restores the node's output format and `waiters.settle` stores the answer |
| Step result unknown | `kind="business"`, action `workflow_unknown_check`, 7 days | approve runs the step again in a new internal conversation (earlier ids kept in `input.previous_attempts`); reject or expiry fails the run with `step_outcome_unknown` |

Every decision is a compare-and-set on the request row, so a duplicate callback is applied
once. Canceling a waiting or unknown run cancels its request.

### 5. A step that was running when the worker died is never run again

If the agent execution behind a running step completed, the stored result is used. If not,
the step and the run become `unknown` and a person decides (section 4). Outside agent steps
there are no side effects in the first version, so this one rule covers every case.

### 6. No separate run time limit in the first version

Every place a run can wait already has a deadline (approval time limit, tool approval
expiry, the 7-day unknown check), and a running agent step follows the existing agent run
limits and cancellation.

### 7. Audit

`workflow.create`, `workflow.publish`, `workflow.archive`, `workflow.run_start`,
`workflow.run_finish` (outcome and reason), `workflow.run_cancel`, `workflow.step_unknown`;
decisions stay `approval.requested` / `approval.decided` / `approval.expired` on the
request. An unknown step is not the end of a run, so it is not `run_finish`.

### 8. AI editing proposes commands, the server builds the draft

`POST /api/workflows/{id}/assist` (W5, `app/workflows/ai_edit.py`) asks the system
`text_primary` model for one `WorkflowEdit` tool call: a question when the request is
ambiguous, or a short plan and edit commands (add, replace or remove a node, connect,
disconnect, set the inputs). The server applies the commands to the draft revision the
person sees, parses the result as a draft and runs the publish checks; a command that does
not apply, a reply that does not validate or a graph the checks reject goes back to the
model once. Nothing is saved: accepting is an ordinary draft save, and publishing checks it
again. The model sees the draft, the person's runnable agents (id, name, description only)
and the turns of this screen, never agent prompts, tools, credentials or run data. The call
is booked to the person (`invoker_type="user"`).

## Consequences

- Loops, parallel branches, merges and LLM or judgment-model decisions need a real graph
  runner; that is when a StateGraph comes back into the design (the decision record already
  uses the `{value, confidence, band, evaluator}` shape of plan 22.5).
- The fingerprint is checked when a run starts. A setting changed while a run waits is used by
  the later steps of that run; only availability is checked per step. Pinning revisions
  (Q1 A) is the way out if that becomes a requirement.
- Owners only (Q5): the runner is the approver. Sharing and approver rules come with the
  OpenFGA `workflow` type.
- The E2E stack starts a job worker next to the backend (`buildJobWorkerCommand` in
  `frontend/scripts/e2e-lane-runtime.mjs`), so every scripted run exercises the real topology.
