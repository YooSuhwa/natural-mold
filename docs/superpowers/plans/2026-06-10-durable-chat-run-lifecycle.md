# Durable Chat Run Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, user-visible chat run lifecycle so Moldy can show running conversations across session navigation, continue generating after stream detach, support real server-side cancel, restore active runs after refresh, and expose the same lifecycle through AG-UI in a dedicated adapter phase.

**Architecture:** Introduce `conversation_runs` as the source of truth for chat execution state, separate browser stream attachment from agent execution, and keep LangGraph persistence anchored on `thread_id = conversation_id`. Existing Moldy SSE events and `message_events` remain the event replay layer during migration; AG-UI is added in P6 as an adapter over the same run lifecycle rather than as the first rewrite.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, PostgreSQL, LangGraph `AsyncPostgresSaver`, `deepagents.create_deep_agent`, existing `EventBroker`, Next.js 16, React 19, TanStack Query, Jotai, Playwright E2E.

---

## Skills Consulted

- `superpowers:writing-plans`: this document follows the staged implementation plan format and requires verification gates before moving between phases.
- `langgraph-persistence`: keep production persistence on Postgres checkpointer, always pass `thread_id`, and treat checkpoint replay/forking as separate from live run execution.
- `deep-agents-core`: Moldy already uses `create_deep_agent`; the plan preserves Deep Agents capabilities such as filesystem backend, skills, subagents, and HITL.
- `langgraph-human-in-the-loop`: interrupt state must use checkpointer + `thread_id`; `interrupted` is a first-class run status and resume must use `Command(resume=...)`.

## Current Code Facts

The plan is based on the current source tree, not a greenfield rewrite.

- Deep Agents are built in `backend/app/agent_runtime/runtime_component_builder.py` via `create_deep_agent(..., checkpointer=get_checkpointer(), ...)`.
- LangGraph config already uses `{"configurable": {"thread_id": cfg.thread_id}}`, where chat runtime sets `cfg.thread_id = conversation_id`.
- The production checkpointer is initialized in `backend/app/agent_runtime/checkpointer.py` with `AsyncPostgresSaver`.
- Chat POST endpoints currently create a `StreamCtx` in `backend/app/services/conversation_stream_service.py` and run `execute_agent_stream(...)` inside the `StreamingResponse` generator.
- Current live reconnect is process-local through `backend/app/agent_runtime/event_broker.py`.
- Current replay is DB-backed through `message_events` and `message_event_chunks` in `backend/app/models/message_event.py`.
- `GET /api/conversations/{conversation_id}/stream?run_id=...` already chooses live broker or DB replay in `backend/app/routers/conversation_messages.py`.
- Frontend `useChatRuntime` currently stores `runIdRef` and `lastEventIdRef` only in React memory.
- The visible Stop button already exists in `frontend/src/components/chat/assistant-thread.tsx` and calls `aui.thread().cancelRun()`, which reaches `useChatRuntime.onCancel()` and only aborts the current fetch stream.
- Current `message_events.status` has only `streaming`, `completed`, `failed`. Keep it that way: `message_events` is the replay log state, not the product run lifecycle. `conversation_runs.status` is the durable product state for detach, cancel, interrupted, and stale behavior.

## Product Requirements

The feature is complete only when all of these are true:

- A conversation that is generating an answer shows a spinning indicator next to its session title, even while the user views another conversation.
- Refreshing the page restores the indicator and re-attaches to the active run when the user returns to that conversation.
- Navigating away from a conversation detaches the browser stream but does not cancel generation.
- Pressing Stop sends a real server-side cancel request and removes the indicator only after the run reaches `canceled` or another terminal state.
- HITL pauses are not shown as "still answering"; they are shown as user action required.
- Backend crash or lost worker state cannot leave an eternal spinner; stale detection converts the run to `stale`.
- Partial answers from canceled or stale runs are preserved with clear status metadata.
- Generated artifacts, token usage, audit logs, trace/debug views, and future AG-UI stream output share the same `run_id`.
- Every phase has backend tests, frontend tests where applicable, and an E2E gate before the next phase starts.

## State Model

`conversation_runs.status` is the durable state machine.

Allowed statuses:

- `queued`: DB row exists; worker has not started agent execution.
- `running`: worker is actively executing LangGraph/Deep Agent code.
- `interrupted`: LangGraph paused for HITL; user input is required.
- `canceling`: user requested stop; worker/task cancellation is in progress.
- `canceled`: stop completed and no further tokens/tool calls should arrive.
- `completed`: answer completed normally.
- `failed`: runtime failed with an error.
- `stale`: server lost the worker or heartbeat before terminal state.

Terminal statuses:

- `completed`
- `failed`
- `interrupted`
- `canceled`
- `stale`

Active statuses:

- `queued`
- `running`
- `canceling`

Important state rules:

- `queued -> running`
- `running -> completed|failed|interrupted|canceling|stale`
- `canceling -> canceled|failed|stale`
- `interrupted -> terminal for that run`; resume creates a new run with `parent_run_id`
- terminal statuses cannot transition back to active
- only one active run is allowed per `conversation_id`

## Run Identity Contract

The existing code already uses a generated `run_id` as the SSE run id, broker key,
`message_events.assistant_msg_id`, artifact `assistant_msg_id`, and Langfuse
`moldy_run_id`. Preserve that contract.

Canonical identity rules:

- `conversation_runs.id` is the externally visible `run_id`.
- `X-Run-Id` returns `str(conversation_runs.id)`.
- `message_start.data.id` equals `str(conversation_runs.id)`.
- `message_events.assistant_msg_id` equals `str(conversation_runs.id)`.
- `conversation_artifacts.assistant_msg_id` equals `str(conversation_runs.id)`.
- Langfuse metadata `moldy_run_id` equals `str(conversation_runs.id)`.
- Persisted chat `messages.id` is not the same value as `run_id`; message linking continues through `message_events.linked_message_ids`.

Do not compare `run_id` directly to frontend `Message.id`. The current frontend
already treats them as different identifiers and uses set-diff plus
`linked_message_ids`-derived persisted messages.

## Message Events Status Policy

Do not expand `message_events.status` for this feature. It remains a replay-log
state with the current values:

- `streaming`: events are still being appended.
- `completed`: the event sequence is closed and replayable.
- `failed`: the event sequence ended through runtime failure or unrecoverable stale finalization.

Product status mapping:

```text
conversation_runs.running     -> message_events.streaming
conversation_runs.completed   -> message_events.completed
conversation_runs.interrupted -> message_events.completed
conversation_runs.canceled    -> message_events.completed
conversation_runs.failed      -> message_events.failed
conversation_runs.stale       -> message_events.failed after appending a stale event when possible
```

For canceled and interrupted runs, use `conversation_runs.status` and event payloads
for user-visible meaning. For example, canceled runs should emit or persist
`message_end.data.status = "canceled"` while `message_events.status` becomes
`completed` because replay is complete.

Trace/debug/share/artifact code that needs product state must join or fetch
`conversation_runs`; it must not infer canceled, interrupted, or stale state from
`message_events.status`.

## AG-UI Strategy

AG-UI should not be the first step. The first step is durable run lifecycle.

The mapping below is the initial adapter target. P6 must first verify the current
official AG-UI package/schema and then adjust exact event names if the SDK differs.

AG-UI mapping in P6:

- `conversation_runs.id` = AG-UI `runId`
- `conversation_id` = AG-UI `threadId`
- `message_start` = `RUN_STARTED` + `TEXT_MESSAGE_START`
- `content_delta` = `TEXT_MESSAGE_CONTENT`
- `tool_call_start` = `TOOL_CALL_START`
- `tool_call_result` = `TOOL_CALL_RESULT`
- `message_end(status=completed)` = `TEXT_MESSAGE_END` + `RUN_FINISHED`
- `message_end(status=failed)` = `RUN_ERROR`
- `message_end(status=canceled)` = `RUN_CANCELLED`
- `interrupt` = HITL/custom AG-UI event
- `stale` = `RUN_ERROR` with stale reason, or a Moldy custom extension event

Do not rewrite the frontend to AG-UI until the run lifecycle is durable. Otherwise event names change while F5, detach, cancel, and stale behavior remain structurally weak.

## Planned Files

Backend files to create:

- `backend/app/models/conversation_run.py`: SQLAlchemy model and status constants.
- `backend/app/schemas/conversation_run.py`: Pydantic response models for active run, run detail, cancel response.
- `backend/app/services/conversation_run_service.py`: status transition rules, active-run lookup, ownership-gated run operations, heartbeat/stale finalization.
- `backend/app/services/conversation_run_worker.py`: detached in-process execution task registry, broker creation, cancellation propagation, finalization.
- `backend/app/routers/conversation_runs.py`: active run, run detail, stream attach, cancel endpoints.
- `backend/app/routers/e2e_chat_run_helpers.py`: E2E-only helpers for seeding/sweeping run state, included only when `E2E_TEST_HELPERS_ENABLED=true`.
- `backend/alembic/versions/m61_conversation_runs.py`: migration for `conversation_runs`.
- `backend/tests/test_conversation_run_service.py`: unit tests for state transitions and active-run constraints.
- `backend/tests/integration/test_conversation_run_lifecycle.py`: integration tests for start, detach, attach, cancel, stale, HITL.

Backend files to modify:

- `backend/app/models/__init__.py`: export `ConversationRun`.
- `backend/app/schemas/conversation.py`: add `active_run` to both `ConversationResponse` and `MessagesEnvelope` so list, detail, and direct refresh re-entry all share the same run contract.
- `backend/app/services/chat_service.py`: hydrate conversation list/page responses with active run state.
- `backend/app/services/conversation_stream_service.py`: split current stream context into reusable run attach helpers; stop owning agent execution inside the response generator.
- `backend/app/routers/conversation_messages.py`: POST message/edit/regenerate/resume should create/start a run and attach to it.
- `backend/app/routers/conversation_branches.py`: edit/regenerate should use the same run lifecycle.
- `backend/app/routers/conversations.py`: include `conversation_runs.router`.
- `backend/app/agent_runtime/event_names.py`: add lifecycle event constants used by Moldy SSE until AG-UI adapter exists.
- `backend/app/agent_runtime/streaming.py`: support `message_end.status = "canceled"` and expose cancellation consistently.
- `backend/app/services/artifact_service.py`: close in-progress artifacts consistently on canceled/stale runs.
- `backend/app/services/audit_service.py` or conversation audit helpers: log run start/cancel/stale/terminal transitions.
- `backend/app/config.py`: add `E2E_TEST_HELPERS_ENABLED` and stale-run timeout settings.
- `backend/app/scheduler.py`: add stale run sweeper job.

Frontend files to create:

- `frontend/src/lib/api/conversation-runs.ts`: active run, run detail, stream attach, cancel API wrappers.
- `frontend/src/lib/hooks/use-conversation-runs.ts`: TanStack Query hooks for active run and polling.
- `frontend/src/lib/stores/chat-runs.ts`: small Jotai overlay for immediately-known local run state, never the source of truth.
- `frontend/e2e/chat-run-lifecycle.spec.ts`: real backend Playwright coverage for spinner, detach, refresh, cancel, stale.

Frontend files to modify:

- `frontend/src/lib/types/index.ts`: `ConversationRun`, `ConversationRunStatus`, `Conversation.active_run`.
- `frontend/src/lib/api/conversations.ts`: conversation list/page types include active run.
- `frontend/src/lib/hooks/use-conversations.ts`: refetch/poll list while active runs exist.
- `frontend/src/lib/sse/stream-chat.ts`: receive run ids from server and attach to run stream.
- `frontend/src/lib/sse/stream-resume-attach.ts`: migrate path to run attach or keep compatibility wrapper.
- `frontend/src/lib/chat/use-chat-runtime.ts`: separate detach from cancel, attach to active run after refresh, consume new lifecycle events.
- `frontend/src/components/chat/conversation-list.tsx`: spinner/status pill next to session title.
- `frontend/src/components/chat/assistant-thread.tsx`: Stop button calls server cancel, not only `AbortController.abort()`.
- `frontend/src/components/chat/reconnect-indicator.tsx`: distinguish reconnecting, detached, canceling, stale.
- `frontend/messages/ko.json` and `frontend/messages/en.json`: all new static copy.
- `frontend/e2e/fixtures.ts`: shared helpers for chat run E2E setup and assertions.

## Phase Gates

Every phase must end with tests for the user-visible or API-visible behavior that
the phase actually introduced. Do not carry skipped E2E items into the next phase.
If a phase has no new frontend code, its Playwright gate should use Playwright's
`request` API against the real dev backend instead of a browser-only assertion.

Common commands:

```bash
cd backend && uv run ruff check .
cd backend && uv run pytest <phase-specific-tests> -q
cd frontend && pnpm lint
cd frontend && pnpm lint:i18n
cd frontend && pnpm lint:design-system
cd frontend && pnpm test <phase-specific-tests> -- --run
cd frontend && pnpm test:e2e <phase-specific-specs> --project=chromium
```

For backend-only phases, frontend unit commands may target the new API client/type
tests only. The Playwright command still runs for the phase-specific lifecycle spec
and should assert the backend contract through real HTTP requests.

For phases that include screenshots, save output under:

```text
output/e2e-captures/<YYYYMMDD>-chat-run-lifecycle/
```

Before sharing screenshots, verify:

```bash
file output/e2e-captures/<YYYYMMDD>-chat-run-lifecycle/*.png
```

## P1: Durable Run Model and APIs

**Outcome:** The backend has a durable `conversation_runs` source of truth, active-run ownership checks, status transitions, and list/read APIs. No detached worker yet.

### Task P1.1: Create Migration and Model

**Files:**

- Create: `backend/alembic/versions/m61_conversation_runs.py`
- Create: `backend/app/models/conversation_run.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_conversation_run_service.py`

- [x] Write migration with table `conversation_runs`.

Required columns:

```python
id UUID primary key
conversation_id UUID not null references conversations(id) on delete cascade
agent_id UUID not null references agents(id) on delete cascade
user_id UUID not null references users(id) on delete cascade
parent_run_id UUID nullable references conversation_runs(id) on delete set null
source VARCHAR(30) not null
status VARCHAR(20) not null
is_active BOOLEAN not null default false
worker_instance_id VARCHAR(80) nullable
interrupt_id VARCHAR(200) nullable
input_preview VARCHAR(500) nullable
last_event_id VARCHAR(80) nullable
error_code VARCHAR(80) nullable
error_message VARCHAR(1000) nullable
cancel_requested_at TIMESTAMP nullable
started_at TIMESTAMP nullable
heartbeat_at TIMESTAMP nullable
completed_at TIMESTAMP nullable
created_at TIMESTAMP not null default now()
updated_at TIMESTAMP not null default now()
metadata_json JSON nullable
```

Required indexes:

```text
ix_conversation_runs_conversation_created (conversation_id, created_at)
ix_conversation_runs_agent_created (agent_id, created_at)
ix_conversation_runs_user_status (user_id, status)
ix_conversation_runs_status_heartbeat (status, heartbeat_at)
uq_conversation_runs_active_conversation partial unique on conversation_id where is_active = true
```

SQLite and PostgreSQL both support partial indexes. In Alembic, create the partial unique index with both `postgresql_where=sa.text("is_active = true")` and `sqlite_where=sa.text("is_active = 1")`.

- [x] Add model constants:

```python
RUN_ACTIVE_STATUSES = ("queued", "running", "canceling")
RUN_TERMINAL_STATUSES = ("completed", "failed", "interrupted", "canceled", "stale")
RUN_STATUS_VALUES = RUN_ACTIVE_STATUSES + RUN_TERMINAL_STATUSES
```

- [x] Run migration tests.

Command:

```bash
cd backend && uv run pytest tests/test_migration_m34.py tests/test_migration_m54.py -q
```

Expected: existing migration tests pass; add a new `tests/test_migration_m61.py` before closing P1.

### Task P1.2: Run Service State Machine

**Files:**

- Create: `backend/app/services/conversation_run_service.py`
- Test: `backend/tests/test_conversation_run_service.py`

- [x] Implement transition guard.

Required behavior:

```python
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "stale", "canceling"},
    "running": {"completed", "failed", "interrupted", "canceling", "stale"},
    "canceling": {"canceled", "failed", "stale"},
    "completed": set(),
    "failed": set(),
    "interrupted": set(),
    "canceled": set(),
    "stale": set(),
}
```

- [x] Implement `create_run(...)`.

Required input:

```python
conversation_id: uuid.UUID
agent_id: uuid.UUID
user_id: uuid.UUID
source: Literal["chat", "start", "edit", "regenerate", "resume"]
input_preview: str | None
parent_run_id: uuid.UUID | None = None
interrupt_id: str | None = None
metadata: dict[str, Any] | None = None
```

Required rules:

- fail with `409` when an active run exists for the conversation
- allow resume only when `parent_run_id` points to the latest `interrupted` run in the same conversation
- when `interrupt_id` is provided for resume, require it to match the parent run's stored `interrupt_id`
- set `is_active=True` for `queued`, `running`, `canceling`
- set `is_active=False` for terminal statuses

- [x] Implement `transition_run(...)`.

Required behavior:

- validate allowed transition
- update `heartbeat_at` for `running`
- set `worker_instance_id` when a worker starts the run
- persist `interrupt_id` when a run reaches `interrupted`
- set `completed_at` for terminal statuses
- clear `is_active` for terminal statuses
- store `error_code` and `error_message` when status is `failed` or `stale`

- [x] Implement tests.

Minimum tests:

```python
async def test_create_run_rejects_second_active_run(...)
async def test_transition_completed_clears_active(...)
async def test_terminal_run_cannot_transition_to_running(...)
async def test_canceling_can_transition_to_canceled(...)
async def test_interrupted_is_terminal_and_resume_creates_new_run(...)
async def test_resume_requires_latest_interrupted_parent_run(...)
async def test_resume_rejects_mismatched_interrupt_id(...)
async def test_get_active_run_is_ownership_scoped(...)
```

Command:

```bash
cd backend && uv run pytest tests/test_conversation_run_service.py -q
```

Expected: all tests pass.

### Task P1.3: Schemas and Read APIs

**Files:**

- Create: `backend/app/schemas/conversation_run.py`
- Create: `backend/app/routers/conversation_runs.py`
- Modify: `backend/app/routers/conversations.py`
- Modify: `backend/app/schemas/conversation.py`
- Modify: `backend/app/services/chat_service.py`
- Test: `backend/tests/test_conversation_runs_router.py`

- [x] Add `ConversationRunResponse`.

Required fields:

```python
id: uuid.UUID
conversation_id: uuid.UUID
agent_id: uuid.UUID
parent_run_id: uuid.UUID | None
status: str
source: str
worker_instance_id: str | None
interrupt_id: str | None
last_event_id: str | None
input_preview: str | None
error_code: str | None
error_message: str | None
cancel_requested_at: UtcDatetime | None
started_at: UtcDatetime | None
heartbeat_at: UtcDatetime | None
completed_at: UtcDatetime | None
created_at: UtcDatetime
updated_at: UtcDatetime
```

- [x] Add endpoints:

```text
GET /api/conversations/{conversation_id}/runs/active
GET /api/conversations/{conversation_id}/runs/{run_id}
```

Required ownership behavior:

- conversation not found and unowned conversation both return the existing not-found style error
- run from another conversation returns not found
- run from another user's conversation returns not found

- [x] Add `active_run: ConversationRunResponse | None` to `ConversationResponse`.

`list_conversations` and `list_conversations_page` must include active run state so the frontend can draw spinners without a second request per row.

- [x] Add `active_run: ConversationRunResponse | None` to `MessagesEnvelope`.

`GET /api/conversations/{conversation_id}/messages` must include active run state
so direct URL refresh can attach to an active run even before the conversation list
query finishes.

- [x] Test list hydration without N+1 query patterns.

Required service shape:

```python
async def active_runs_for_conversations(
    db: AsyncSession,
    conversation_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ConversationRun]:
```

- [x] Run P1 backend gate.

Command:

```bash
cd backend && uv run pytest \
  tests/test_conversation_run_service.py \
  tests/test_conversation_runs_router.py \
  tests/test_conversations_router.py \
  -q
```

Expected: all pass.

### P1 E2E Gate

P1 is backend-visible, so its E2E gate is a Playwright API contract test against
the real dev backend. It must not be skipped.

**Files:**

- Create: `frontend/e2e/chat-run-lifecycle.spec.ts`
- Create: `backend/app/routers/e2e_chat_run_helpers.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`

Test scenario:

- require `E2E_TEST_HELPERS_ENABLED=true`; helper routes return `404` when disabled
- create or reuse an E2E agent and conversation using normal authenticated APIs
- seed a `conversation_runs` row through an E2E-only helper:

```text
POST /api/e2e/conversations/{conversation_id}/runs
```

Request body:

```json
{
  "status": "running",
  "source": "chat",
  "input_preview": "P1 active run contract"
}
```

- call `GET /api/agents/{agent_id}/conversations/page`
- assert the target conversation has `active_run.status = "running"`
- assert another user's conversation/run is not visible through the same API
- call `GET /api/conversations/{conversation_id}/runs/active`
- assert the returned `id` equals the seeded run id

Command:

```bash
cd frontend && E2E_TEST_HELPERS_ENABLED=true \
  pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium
```

Expected: Playwright passes against the real FastAPI backend and no P1 lifecycle
assertion is skipped.

## P2: Detached Execution and Re-Attach

**Outcome:** Creating a chat message starts a backend-owned run task. Browser streams attach to the run; disconnecting the stream does not cancel execution.

### Task P2.1: Worker Registry and Run Start

**Files:**

- Create: `backend/app/services/conversation_run_worker.py`
- Modify: `backend/app/services/conversation_stream_service.py`
- Modify: `backend/app/routers/conversation_messages.py`
- Modify: `backend/app/routers/conversation_branches.py`
- Test: `backend/tests/integration/test_conversation_run_lifecycle.py`

- [x] Implement `RunTaskRegistry`.

Required API:

```python
class RunTaskRegistry:
    @property
    def worker_instance_id(self) -> str: ...
    def start(self, run_id: uuid.UUID, task: asyncio.Task[None]) -> None: ...
    def get(self, run_id: uuid.UUID) -> asyncio.Task[None] | None: ...
    def cancel(self, run_id: uuid.UUID) -> bool: ...
    def discard(self, run_id: uuid.UUID) -> None: ...
    async def shutdown(self, timeout_seconds: float = 10.0) -> None: ...
```

Registry ownership rules:

- create exactly one registry during FastAPI lifespan startup
- expose it through a dependency or module-level accessor used by run routers
- generate one stable `worker_instance_id` per process
- store `worker_instance_id` on `conversation_runs` when the worker transitions the run to `running`
- add a task done callback that discards the task and logs unexpected exceptions
- on app shutdown, cancel all active local tasks, wait up to `timeout_seconds`, close brokers, and transition owned non-terminal runs to `stale` if they did not finalize
- every worker task must open fresh DB sessions through `async_session`; it must not use a request-scoped `AsyncSession`

- [x] Implement `start_conversation_run(...)`.

Required behavior:

- create `EventBroker` keyed by `run_id`
- create partial flush callback using existing `trace_storage.append_events`
- start an `asyncio.create_task(...)` that runs `execute_agent_stream` or `resume_agent_stream`
- worker owns finalization, status transitions, broker close, artifact finalization
- StreamingResponse only attaches to the broker/DB event stream
- worker catches `Exception` and transitions the run to `failed`
- worker catches `asyncio.CancelledError` only for explicit cancel/shutdown paths and transitions to `canceled` or `stale` according to the requester
- worker updates `conversation_runs.last_event_id` whenever the partial flush callback sees a newer SSE event id
- worker finalization must call `trace_storage.finalize_turn` with `message_events.status` mapped through the Message Events Status Policy section

- [x] Keep existing `X-Run-Id` response header.

The frontend already reads it in `frontend/src/lib/sse/parse-sse.ts`.

### Task P2.2: Attach Stream Endpoint

**Files:**

- Modify: `backend/app/routers/conversation_runs.py`
- Modify: `backend/app/routers/conversation_messages.py`
- Test: `backend/tests/integration/test_conversation_run_lifecycle.py`

- [x] Add endpoint:

```text
GET /api/conversations/{conversation_id}/runs/{run_id}/stream?last_event_id=...
```

Required behavior:

- if broker is live, attach live
- if broker is closed and run is terminal, replay DB events
- if run is active, no local broker exists, and `heartbeat_at` is newer than the stale threshold, return a retryable attach error with `Retry-After: 1`
- if run is active, no local broker exists, and `heartbeat_at` is older than the stale threshold, transition the run to `stale` and emit/replay a stale marker
- if run is active and `worker_instance_id` does not match this process, treat that as unsupported multi-worker routing and return the same retryable attach error until the stale threshold is reached
- preserve legacy `GET /api/conversations/{conversation_id}/stream?run_id=...` as compatibility wrapper until AG-UI migration

- [x] Add tests.

Minimum tests:

```python
async def test_post_returns_run_id_and_worker_continues_after_client_disconnect(...)
async def test_attach_live_run_receives_tail_after_initial_stream_detach(...)
async def test_completed_run_replays_from_message_events(...)
async def test_active_run_without_worker_and_fresh_heartbeat_returns_retryable_attach_error(...)
async def test_active_run_without_worker_and_stale_heartbeat_becomes_stale(...)
async def test_worker_shutdown_marks_owned_active_run_stale(...)
```

### Task P2.3: Convert Message/Edit/Regenerate/Resume to Run Creation

**Files:**

- Modify: `backend/app/routers/conversation_messages.py`
- Modify: `backend/app/routers/conversation_branches.py`
- Test: `backend/tests/test_conversations_router.py`
- Test: `backend/tests/test_thread_branch.py`

Required mapping:

```text
POST /api/agents/{agent_id}/conversations/start -> source="start"
POST /api/conversations/{conversation_id}/messages -> source="chat"
POST /api/conversations/{conversation_id}/messages/resume -> source="resume"
POST /api/conversations/{conversation_id}/messages/edit -> source="edit"
POST /api/conversations/{conversation_id}/messages/regenerate -> source="regenerate"
```

Existing behavior to preserve:

- draft first message still creates exactly one conversation
- edit/regenerate branch checkpoint behavior remains unchanged
- HITL resume still uses `Command(resume=...)`
- `message_events.assistant_msg_id` equals `run_id`
- persisted chat `messages.id` does not need to equal `run_id`; continue linking with `message_events.linked_message_ids`

### P2 E2E Gate

Create a real backend Playwright scenario in `frontend/e2e/chat-run-lifecycle.spec.ts`.

Needed scripted model support:

- Extend `backend/app/agent_runtime/e2e_scripted_model.py` with a marker such as `E2E_SLOW_STREAM`.
- The model should emit a deterministic answer slowly enough for Playwright to navigate away during generation.
- The model must be dev-only behind `E2E_SCRIPTED_MODEL_ENABLED=true`.

E2E scenario:

1. Create an agent using the `e2e_scripted` model.
2. Open conversation A.
3. Send `E2E_SLOW_STREAM`.
4. Wait for conversation A spinner in the list.
5. Navigate to conversation B while A is still running.
6. Assert A spinner remains.
7. Navigate back to A.
8. Assert the stream re-attaches and final answer completes.
9. Assert spinner disappears.
10. Assert there are no console/page/network errors.

Command:

```bash
cd frontend && pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium
```

Expected: test passes with real FastAPI backend and `E2E_SCRIPTED_MODEL_ENABLED=true`.

## P3: Server-Side Cancel, HITL, and Stale Recovery

**Outcome:** Stop button cancels the server run. HITL and stale runs have precise states and user-visible behavior.

### Task P3.1: Cancel API

**Files:**

- Modify: `backend/app/routers/conversation_runs.py`
- Modify: `backend/app/services/conversation_run_service.py`
- Modify: `backend/app/services/conversation_run_worker.py`
- Test: `backend/tests/integration/test_conversation_run_lifecycle.py`

- [x] Add endpoint:

```text
POST /api/conversations/{conversation_id}/runs/{run_id}/cancel
```

Required behavior:

- requires CSRF
- ownership-gated through conversation ownership
- active `queued` or `running` run transitions to `canceling`
- repeated cancel for `canceling` returns the current run status without creating a second cancellation path
- cancel for terminal runs returns the terminal run status without restarting work
- local worker task receives `task.cancel()`
- worker catches `asyncio.CancelledError`
- final status becomes `canceled`
- broker emits terminal lifecycle event
- `message_events` finalization records the partial event sequence

### Task P3.2: Runtime Cancellation Semantics

**Files:**

- Modify: `backend/app/agent_runtime/streaming.py`
- Modify: `backend/app/agent_runtime/skill_executor.py`
- Modify: `backend/app/agent_runtime/mcp_tool_loader.py` if MCP runtime tools need cancellation cleanup.
- Modify: `backend/app/services/artifact_service.py`
- Test: `backend/tests/test_streaming.py`
- Test: `backend/tests/test_skill_executor_node.py`
- Test: `backend/tests/test_artifact_service.py`

Required behavior:

- `CancelledError` must not be converted into a generic provider error.
- `message_end` may carry `status: "canceled"`.
- canceled runs finalize `conversation_runs.status = "canceled"` and `message_events.status = "completed"`.
- failed runs finalize `conversation_runs.status = "failed"` and `message_events.status = "failed"`.
- stale runs finalize `conversation_runs.status = "stale"` and `message_events.status = "failed"` after appending a `stale` event when possible.
- skill subprocesses are terminated on cancellation.
- incomplete artifact rows produced by canceled runs are marked `failed`; do not add a new artifact status unless an existing DB constraint requires it and the migration is included in P5.

### Task P3.3: HITL State

**Files:**

- Modify: `backend/app/services/conversation_run_worker.py`
- Modify: `backend/app/routers/conversation_messages.py`
- Test: `backend/tests/test_hitl_wire.py`
- Test: `backend/tests/integration/test_conversation_run_lifecycle.py`

Required behavior:

- when the stream event sequence contains an `interrupt` event, run transitions to `interrupted` even though current `streaming.py` also emits `message_end`
- `is_active=False` for interrupted runs, so spinner stops
- conversation list shows action-required state, not answer-running state
- resume creates a new run with `source="resume"` and `parent_run_id` set to interrupted run id
- resume requires the current `interrupt_id` to match the parent run when an interrupt id is available
- current stale interrupt guard in `GET /stream` remains compatible

### Task P3.4: Stale Sweeper

**Files:**

- Modify: `backend/app/scheduler.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/conversation_run_service.py`
- Test: `backend/tests/test_scheduler_extended.py`
- Test: `backend/tests/integration/test_conversation_run_lifecycle.py`

Required behavior:

- active run with stale heartbeat transitions to `stale`
- stale threshold is configurable; default dev value should be generous enough not to interrupt normal long runs
- application startup marks orphaned active runs stale when no worker registry owns them
- startup orphan sweep only marks runs owned by this process' `worker_instance_id`; runs without `worker_instance_id` are marked stale only when heartbeat is already older than threshold
- stale status clears spinner
- stale event tells frontend partial output may be incomplete

### P3 E2E Gate

Add scenarios to `frontend/e2e/chat-run-lifecycle.spec.ts`.

Cancel scenario:

1. Start slow run.
2. Assert spinner appears.
3. Press Stop.
4. Assert cancel request is sent.
5. Assert spinner shows canceling briefly or the Stop button disables.
6. Assert spinner disappears.
7. Assert partial answer remains with "중단됨" / "Canceled" status.
8. Assert `GET /runs/{run_id}` returns `status="canceled"`.

HITL scenario:

1. Trigger a deterministic HITL run.
2. Assert spinner appears while generating.
3. Assert spinner disappears when approval card appears.
4. Assert conversation list shows action-required indicator.
5. Submit approval.
6. Assert new run starts and completes.

Stale scenario:

1. Seed an active run via API.
2. Mark heartbeat older than threshold through an E2E-only helper route enabled only when `E2E_TEST_HELPERS_ENABLED=true`.
3. Wait for sweeper or call test-only stale sweep route.
4. Assert spinner disappears and stale copy is visible.

E2E helper safety:

- helper routes must not be registered unless `E2E_TEST_HELPERS_ENABLED=true`
- helper routes must require the authenticated E2E user
- helper routes must reject non-local origins through the same CORS/auth path as normal APIs
- production and normal dev runs must see `404` for every `/api/e2e/*` route

## P4: Frontend Running Indicator and Re-Entry UX

**Outcome:** Users see correct session-level state across navigation and refresh.

### Task P4.1: Types and API Hooks

**Files:**

- Modify: `frontend/src/lib/types/index.ts`
- Create: `frontend/src/lib/api/conversation-runs.ts`
- Create: `frontend/src/lib/hooks/use-conversation-runs.ts`
- Modify: `frontend/src/lib/api/conversations.ts`
- Test: `frontend/src/lib/hooks/__tests__/use-conversation-runs.test.tsx`

Required types:

```ts
export type ConversationRunStatus =
  | 'queued'
  | 'running'
  | 'interrupted'
  | 'canceling'
  | 'canceled'
  | 'completed'
  | 'failed'
  | 'stale'

export interface ConversationRun {
  id: string
  conversation_id: string
  agent_id: string
  status: ConversationRunStatus
  source: string
  last_event_id: string | null
  input_preview: string | null
  error_code: string | null
  error_message: string | null
  cancel_requested_at: string | null
  started_at: string | null
  heartbeat_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}
```

Add:

```ts
active_run?: ConversationRun | null
```

to `Conversation`.

Also add:

```ts
active_run?: ConversationRun | null
```

to `MessagesEnvelope`.

### Task P4.2: Conversation List Spinner

**Files:**

- Modify: `frontend/src/components/chat/conversation-list.tsx`
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`
- Test: `frontend/src/components/chat/__tests__/conversation-list-run-state.test.tsx`

Required UI:

- small `Loader2` spinner next to title when `active_run.status` is `queued`, `running`, or `canceling`
- no spinner for `interrupted`; use a compact attention marker
- no spinner for terminal statuses
- active conversation styling must remain readable
- use `lucide-react` icons
- all visible text must use `next-intl`
- no raw hex, `rounded-xl`, `shadow-*`, `transition-all`, or inline style

### Task P4.3: Re-Attach on Conversation Entry

**Files:**

- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
- Modify: `frontend/src/lib/sse/stream-resume-attach.ts`
- Modify: `frontend/src/lib/sse/stream-chat.ts`
- Test: `frontend/src/lib/chat/__tests__/use-chat-runtime-active-run.test.tsx`

Required behavior:

- on mount, if `conversation.active_run` exists and status is active, attach to its stream
- if route changes away, detach stream without calling cancel
- if browser refreshes and active run exists, attach again
- dedup event ids using existing `streamGuard`
- if attach returns replay mode, rebuild current streaming message from replayed events
- if attach returns stale, stop running state and show stale message
- invalidate/refetch conversation list and active run detail when terminal lifecycle events arrive
- poll conversation pages while at least one page item has an active run, with a 1-2 second interval and no polling when no active runs exist

### Task P4.4: Stop Button Calls Cancel API

**Files:**

- Modify: `frontend/src/components/chat/assistant-thread.tsx`
- Modify: `frontend/src/components/chat/builder-overrides.tsx`
- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
- Test: `frontend/src/lib/chat/__tests__/use-chat-runtime-cancel.test.tsx`

Required behavior:

- Stop button invokes server cancel when a `run_id` is known
- AbortController is used after the cancel request is accepted to detach the local stream
- if the cancel request fails, keep the local stream attached unless the user navigates away
- if no `run_id` exists, fallback to local abort and show a non-fatal warning in console only
- disable Stop while cancel request is in flight
- do not create duplicate toast stacks

### P4 E2E Gate

Run:

```bash
cd frontend && pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium
```

Required Playwright assertions:

- spinner appears next to the correct session, not all sessions
- spinner continues while another session is open
- F5 restores spinner and active run attach
- Stop removes spinner only after server status becomes `canceled`
- interrupted state does not show spinner
- no JS exceptions
- no unexpected request failures

## P5: Artifacts, Usage, Audit, and Observability Hardening

**Outcome:** Durable runs are trustworthy under tool calls, generated files, costs, and debugging workflows.

### Task P5.1: Artifact Finalization

**Files:**

- Modify: `backend/app/services/artifact_service.py`
- Modify: `backend/app/models/conversation_artifact.py` only if tests prove the existing `failed` status cannot represent incomplete canceled/stale outputs.
- Modify: `backend/alembic/versions/m62_artifact_status_hardening.py` only if a DB check constraint change is unavoidable.
- Test: `backend/tests/test_artifact_service.py`
- Test: `frontend/e2e/document-artifact-viewers.spec.ts`

Required behavior:

- completed run links ready artifacts to final assistant messages
- canceled/stale run does not expose incomplete files as ready
- deleted/failed artifact behavior remains compatible
- prefer existing artifact status `failed` for incomplete canceled/stale outputs; do not add `canceled` artifact status unless tests prove the existing `failed` state cannot express the UI requirement

### Task P5.2: Token Usage for Canceled Runs

**Files:**

- Modify: `backend/app/agent_runtime/streaming.py`
- Modify: `backend/app/hooks/builtin/spend_hook.py`
- Modify: `backend/app/services/chat_service.py`
- Test: `backend/tests/test_streaming.py`
- Test: `backend/tests/test_usage_service.py`
- Test: `frontend/e2e/chat-token-usage.spec.ts`

Required behavior:

- usage emitted before cancel is stored
- missing usage for canceled run does not crash hooks
- `total_estimated_cost` remains correct after refresh

### Task P5.3: Audit Events

**Files:**

- Modify: `backend/app/services/conversation_audit_service.py`
- Modify: `backend/app/services/audit_service.py` only if new helper shape is needed.
- Test: `backend/tests/test_audit_integration.py`

Required events:

```text
conversation.run_start
conversation.run_complete
conversation.run_cancel_request
conversation.run_canceled
conversation.run_interrupted
conversation.run_stale
conversation.run_failed
```

Each audit event must include:

```json
{
  "run_id": "...",
  "conversation_id": "...",
  "agent_id": "...",
  "source": "chat|start|edit|regenerate|resume",
  "status": "..."
}
```

### Task P5.4: Debug Trace Correlation

**Files:**

- Modify: `backend/app/services/trace_debug_service.py`
- Modify: `backend/app/routers/conversation_traces.py`
- Test: `backend/tests/test_trace_debug_api.py`

Required behavior:

- trace list shows canceled/stale runs with clear status
- `moldy_run_id` matches `conversation_runs.id`
- fallback `message_events` trace remains readable

### P5 E2E Gate

Run:

```bash
cd frontend && pnpm test:e2e \
  e2e/chat-run-lifecycle.spec.ts \
  e2e/document-artifact-viewers.spec.ts \
  e2e/chat-token-usage.spec.ts \
  --project=chromium
```

Required assertions:

- cancel during artifact generation leaves no broken ready artifact card
- completed artifact run still shows preview
- token usage UI still works after completed run
- canceled run does not produce corrupted token UI

## P6: AG-UI Adapter and Gradual Frontend Migration

**Outcome:** AG-UI stream support sits on top of the durable run lifecycle without breaking existing Moldy SSE routes.

### Task P6.0: Verify AG-UI Contract

**Files:**

- Modify: `frontend/package.json` if an AG-UI client package is selected.
- Modify: `backend/pyproject.toml` if a backend AG-UI package is selected.
- Create: `docs/design-docs/adr-020-chat-run-ag-ui-adapter.md`

Required behavior:

- identify the official AG-UI package and event schema current at implementation time
- document the exact event names, required fields, and cancellation semantics in ADR-020
- decide whether AG-UI is backend-emitted SSE, frontend adapter-only, or both
- keep `conversation_runs.id` as AG-UI `runId`
- keep `conversation_id` as AG-UI `threadId`
- keep Moldy SSE as the default protocol until P6 E2E passes in both modes

### Task P6.1: Backend AG-UI Event Adapter

**Files:**

- Create: `backend/app/agent_runtime/ag_ui_adapter.py`
- Create: `backend/app/routers/conversation_ag_ui.py`
- Modify: `backend/app/routers/conversations.py`
- Test: `backend/tests/test_ag_ui_adapter.py`
- Test: `backend/tests/integration/test_conversation_ag_ui_stream.py`

Required adapter behavior:

- convert stored Moldy events to AG-UI event stream
- convert live broker events to AG-UI event stream
- preserve `runId` and `threadId`
- emit cancel/interrupted/stale status faithfully
- keep existing Moldy SSE endpoints working

### Task P6.2: Frontend AG-UI Consumer Behind Feature Flag

**Files:**

- Create: `frontend/src/lib/ag-ui/chat-run-consumer.ts`
- Modify: `frontend/src/lib/chat/use-chat-runtime.ts` or create a sibling `use-ag-ui-chat-runtime.ts`
- Modify: `frontend/src/lib/types/index.ts`
- Test: `frontend/src/lib/ag-ui/__tests__/chat-run-consumer.test.ts`

Feature flag:

```text
NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=moldy_sse|ag_ui
```

Required behavior:

- default remains `moldy_sse`
- `ag_ui` path handles content, tool calls, cancel, interrupted, stale, and completed runs
- user-visible behavior remains identical between protocols

### P6 E2E Gate

Run the same lifecycle E2E twice:

```bash
cd frontend && NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=moldy_sse \
  pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium

cd frontend && NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=ag_ui \
  pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium
```

Expected:

- both protocols pass the same user-visible assertions
- no duplicate backend run creation
- cancel and refresh behavior match

## Full Regression Gate

Before calling the feature complete:

```bash
cd backend && uv run ruff check .
cd backend && uv run pytest -q
cd frontend && pnpm lint
cd frontend && pnpm lint:i18n
cd frontend && pnpm lint:design-system
cd frontend && pnpm test -- --run
cd frontend && pnpm build
cd frontend && pnpm test:e2e e2e/chat-run-lifecycle.spec.ts --project=chromium
```

If the change touches artifacts or token usage:

```bash
cd frontend && pnpm test:e2e \
  e2e/document-artifact-viewers.spec.ts \
  e2e/chat-token-usage.spec.ts \
  --project=chromium
```

## Manual QA Checklist

- Start a slow answer; spinner appears only on that session.
- Navigate to another conversation; spinner remains on the original session.
- Return before completion; tokens continue streaming.
- Refresh during generation; spinner restores and stream attaches.
- Press Stop; server status becomes `canceling`, then `canceled`.
- Start a new message after cancel; it is allowed and creates a new run.
- Trigger HITL; spinner stops and action-required UI appears.
- Approve HITL; a resume run starts and completes.
- Kill/restart backend during a run; the stale sweeper clears the spinner and shows stale state.
- Generate an artifact normally; preview works.
- Cancel during artifact generation; no broken ready artifact appears.
- Check audit page; run start/cancel/stale events are visible with run id.
- Check trace page; `moldy_run_id` links to the run id.

## Risk Register

- **Process-local broker limitation:** current `EventBroker` is process-local. For a single dev server this is acceptable. For multi-worker deployment, sticky routing or Redis pub/sub is required before enabling multiple backend workers.
- **True restart continuation:** in-process `asyncio` workers cannot continue through process death. This plan marks orphaned runs stale after restart. Full continuation across process death requires an external durable task queue.
- **Cancel propagation depth:** cancelling the top-level task is not enough if subprocesses, MCP calls, or HTTP clients ignore cancellation. P3 explicitly covers subprocess and tool cleanup.
- **Partial answer semantics:** canceled and stale partial answers must be visually distinct from completed answers.
- **HITL semantics:** an interrupted run is not "still running"; showing a spinner there would be misleading.
- **Duplicate active run:** DB partial unique index and service-level checks both exist because frontend race conditions and multi-tab behavior are realistic.
- **AG-UI migration scope:** AG-UI is a protocol adapter phase, not the foundation. The foundation is the run lifecycle.

## Implementation Order

Recommended commit order:

1. `feat(chat-runs): add durable conversation run model`
2. `feat(chat-runs): expose active run APIs`
3. `feat(chat-runs): detach stream from execution`
4. `feat(chat-runs): support server-side cancel`
5. `feat(chat-runs): handle interrupted and stale runs`
6. `feat(chat-ui): show conversation run indicators`
7. `feat(chat-ui): reattach active runs after refresh`
8. `test(e2e): cover durable chat run lifecycle`
9. `feat(chat-runs): harden artifacts usage and audit`
10. `feat(ag-ui): add chat run adapter`

## Self-Review

- Spec coverage: spinner across sessions, refresh reattach, cancel, HITL, stale, artifacts, token usage, audit, and AG-UI are each mapped to a phase.
- Source alignment: the plan preserves existing `thread_id = conversation_id`, `AsyncPostgresSaver`, `EventBroker`, `message_events`, `useChatRuntime`, and Playwright scripted model paths.
- Test coverage: every phase has backend and E2E gates; user-visible phases require Playwright.
- Type consistency: `ConversationRun`, `ConversationRunStatus`, `active_run`, `run_id`, `thread_id`, and `conversation_id` names are used consistently.
- No shortcut accepted: local frontend atom is only an overlay; server `conversation_runs` is the source of truth.

## Implementation Status and Deviations (2026-06-11)

P1~P6 구현이 `codex/chat-run-lifecycle` 브랜치에 반영되었다. 코드 리뷰 3라운드
(major 4건·minor 5건 수정, 적대적 재검증 통과)를 거쳤으며, 아래 편차와 후속
항목이 확정되었다.

계획 대비 의도된 편차:

- `message_end(status=canceled)` 는 AG-UI `RUN_CANCELLED` 대신
  `TEXT_MESSAGE_END` + `RUN_FINISHED(result.status="canceled")` 로 매핑.
  공식 AG-UI core event 에 `RUN_CANCELLED` 가 없어 P6.0 검증 단계에서 조정
  (ADR-020 매핑 표가 기준).
- `frontend/src/lib/stores/chat-runs.ts`(Jotai overlay) 대신 순수 헬퍼
  `frontend/src/lib/chat-runs/status.ts` + 기존 `chat-store.ts` 의
  `chatCancelInFlightAtom` 으로 구현. 서버 `conversation_runs` 가 유일한
  source of truth 라는 원칙은 동일하게 유지.
- `reconnect-indicator.tsx` 의 4-상태(reconnecting/detached/canceling/stale)
  세분화는 미적용. reconnecting/idle + stale toast + Stop 비활성 + canceled
  notice 조합으로 제품 요구사항을 충족 — UX 필요가 관찰되면 후속 진행.
- 테스트 위치 편차: `tests/integration/test_conversation_ag_ui_stream.py` 의
  커버리지는 `tests/test_conversation_runs_router.py` 에,
  `use-chat-runtime-cancel.test.tsx` 는 `use-chat-runtime-commit.test.tsx` 에 포함.

계획 외 보강(리뷰 라운드에서 추가):

- 프론트 attach/send race 가드(`streamInFlightRef`, `consumedRunIdRef`),
  unmount 시 stream guard 토큰 무효화.
- broker ring buffer 에서 `last_event_id` evict 시 silent gap 대신
  `stale(reason="broker_gap")` 마커 + buffer 잔여분 replay
  (Moldy `/runs/{id}/stream` 과 `/ag-ui-stream` 공통).
- run cancel 시 skill subprocess 가 고아로 남지 않도록 `skill_executor.py` 에
  CancelledError kill 경로 추가 (P3.2 cancel propagation 충족).
- F5 refresh 복원 E2E 시나리오 추가 (P4 게이트).
- `streamSSEPost`(parse-sse.ts) abort deadlock 수정: fetch-event-source 는 input
  signal abort 시 promise 를 reject 가 아니라 resolve 하므로 bridge 의 catch 만으로는
  closed 가 세워지지 않아 소비 루프가 영원히 대기 — Stop 후 isRunning 미해제/취소
  notice 미표시의 근본 원인. abort 를 AbortError 로 변환하는 finally 추가
  (P3 cancel E2E 게이트가 이 수정으로 처음 통과).

남은 후속 항목 (ag_ui 플래그 활성화 전 필수):

- [ ] P6 E2E 이중 프로토콜 게이트 — `NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=ag_ui` 로
      lifecycle spec 을 한 번 더 통과시키는 것을 플래그 활성화 PR 의 게이트로 한다.
- [ ] AG-UI gap degrade 시 합성 `TEXT_MESSAGE_START` 주입 검토
      (ADR-020 Tradeoffs 의 한계 항목).
