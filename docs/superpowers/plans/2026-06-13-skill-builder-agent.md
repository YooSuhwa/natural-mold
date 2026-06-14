# Skill Builder Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a hidden, conversational Skill Builder Agent that helps users create and improve high-quality portable `.skill` packages inside Moldy, validates them, supports human review, and finalizes changes into the existing Skill system.

**Architecture:** Add a `skill_builder_sessions` workflow parallel to the existing Agent Builder, but finalize into a `Skill` row or an existing `Skill` update instead of an `Agent`. Use LangGraph for deterministic product orchestration, optionally use Deep Agents as the draft/revision worker where file planning is useful, use the existing system LLM resolver for hidden-agent text generation, existing skill package storage for persistence, and the current runtime/credential surfaces for validation and evaluation. Keep generated and improved packages portable: `SKILL.md` plus optional `scripts/`, `references/`, `assets/`, and platform metadata under `agents/`.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, PostgreSQL JSON columns, LangGraph, existing `create_chat_model` / `resolve_system_model`, existing `app.skills` package storage, existing marketplace secret scan, Next.js 16, React 19, TanStack Query, builder-specific SSE helpers, LangGraph v3 chat-runtime compatibility constraints, next-intl.

---

## Skills And Source References

- `superpowers:writing-plans`: this document is saved under `docs/superpowers/plans/` and uses task checkboxes for implementation tracking.
- `framework-selection`: this feature needs custom control flow, persisted session state, preview, approval, validation, and optional eval loops. Use **LangGraph** as the outer workflow. Do not create a normal user-visible `Agent` row for the builder itself.
- LangChain `framework-selection`: Deep Agents are appropriate for open-ended work that needs planning, file management, subagents, and on-demand skills; LangGraph is appropriate when the product must own exact state transitions and approval gates.
- LangChain `deep-agents-core`: Deep Agents provide TodoList, Filesystem, SubAgent, Skills, Memory, and HITL middleware through `create_deep_agent`.
- LangChain `deep-agents-memory`: in web servers, avoid unrestricted `FilesystemBackend`; use State/Store-backed or sandboxed draft storage instead of direct disk writes.
- LangChain `deep-agents-orchestration`: subagents are good for isolated grading/analyzing/comparing, but custom subagents must receive skills explicitly and should get complete instructions in one call.
- Local Codex skill creator: `/Users/chester/.codex/skills/.system/skill-creator/SKILL.md`
  - Strong at portable package structure, progressive disclosure, `agents/openai.yaml`, resource folder selection, and concise `SKILL.md` writing.
- Local Claude Code skill creator: `/Users/chester/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/SKILL.md`
  - Strong at eval loops, with-skill vs baseline comparison, grader/analyzer/comparator agents, HTML review, and description trigger optimization.
- Vercel Agent Skills docs: `https://vercel.com/docs/agent-resources/skills`
  - Confirms skills are intended as modular, installable capabilities that work across many agent tools.

Both installed skill-creator packages contain Apache 2.0 license files. If implementation copies source code instead of reimplementing behavior, keep the license notice. The preferred first pass is to reimplement the small needed behavior in Moldy style.

## Current Code Facts

This plan is based on the current source tree.

- Skill rows already persist filesystem-backed text/package skills in `backend/app/models/skill.py:59`.
- `Skill` already has `credential_requirements` and `execution_profile` JSON columns in `backend/app/models/skill.py:128`. The builder should use these existing columns.
- Text skill creation currently accepts only `name`, `slug`, `description`, `content`, `version` through `backend/app/schemas/skill.py:23`.
- Package skill upload currently goes through `POST /api/skills/upload` in `backend/app/routers/skills.py:187` and `skill_service.create_package_skill(...)` in `backend/app/skills/service.py:134`.
- Package extraction already rejects oversized archives, bad ZIPs, symlinks, absolute paths, null bytes, and zip-slip paths in `backend/app/skills/packager.py:81`.
- `SKILL.md` parsing currently requires only non-empty `name` and `description` when `require_metadata=True` in `backend/app/skills/inspector.py:29`.
- Existing file-level package editing lives in `backend/app/routers/skills.py:412`, `:454`, and `:488`.
- Package skill creation now stores a full package tree hash through `compute_package_tree_hash(...)` in `backend/app/skills/service.py:166` and `backend/app/skills/package_hash.py:24`.
- Package file mutation helpers now live in `backend/app/skills/file_service.py:31` and `backend/app/skills/file_service.py:53`; both call `refresh_package_metadata(...)`, which refreshes `size_bytes`, file metadata, and `skill.content_hash` in `backend/app/skills/package_metadata.py:9`.
- Existing skill credential requirement reading and binding APIs live in `backend/app/routers/skills.py:537`.
- Existing runtime descriptor creation includes `execution_profile` through `backend/app/skills/runtime.py:18`.
- Existing `execute_in_skill` enforces selected-skill directories, subprocess allowlist, timeout, and credential env injection in `backend/app/agent_runtime/skill_executor.py:131`.
- Existing runtime credential resolution fails fast on missing required user bindings through `resolve_runtime_credentials(...)` in `backend/app/marketplace/skill_runtime.py:284`.
- Existing skill credential validation rejects cross-user credentials, system credentials, definition mismatches, and unknown requirement keys in `backend/app/marketplace/credential_requirements.py:153`.
- Existing skill credential binding mutations already write `skill.credential_binding_upsert` and `skill.credential_binding_delete` audit events in `backend/app/routers/skills.py:618` and `backend/app/routers/skills.py:659`.
- Existing redaction helpers are `redact_credential_values(...)` and `redact_keys(...)` in `backend/app/marketplace/redaction.py:51` and `backend/app/marketplace/redaction.py:88`.
- Existing global audit events are stored through `audit_service.record_event(...)` in `backend/app/services/audit_service.py:86`; metadata is sanitized with `redact_keys(...)` in `backend/app/services/audit_service.py:57`.
- Existing credential audit writes go through `credential_service.write_audit_log(...)` in `backend/app/credentials/service.py:267`, which also writes a matching global `credential.<action>` audit event in `backend/app/credentials/service.py:290`.
- Existing `CredentialAuditLog` rows are append-only credential lifecycle/use records in `backend/app/models/credential_audit_log.py:14`.
- Existing runtime tool dependencies read `execution_profile.tool_dependencies` in `backend/app/agent_runtime/skill_tool_dependencies.py:16`.
- Existing artifact versioning uses `ConversationArtifact.current_version_id` plus immutable `ArtifactVersion` rows in `backend/app/models/conversation_artifact.py:111` and `backend/app/models/conversation_artifact.py:140`.
- Existing artifact writes increment `ArtifactVersion.version_number` and update the current pointer in `backend/app/services/artifact_service.py:287`.
- Skills currently have no dedicated revision/rollback table; this plan adds `skill_revisions` using the artifact versioning shape.
- Agent Builder already provides the session + SSE + confirm pattern in `backend/app/routers/builder.py:61`, `:160`, `:188`, and `backend/app/services/builder_service.py:73`, `:392`.
- Assistant already provides a hidden internal agent pattern using `resolve_system_model(db, "text_primary")` and `build_agent(...)` in `backend/app/agent_runtime/assistant/assistant_agent.py:55`.
- `resolve_system_model(db, "text_primary")` raises `SystemModelNotConfiguredError` when the system LLM role, model, or credential is missing in `backend/app/services/system_credential_resolver.py:37`. Skill Builder must handle this as a product readiness state, not as a generic 500.
- LangGraph checkpointer setup now uses explicit pool sizing in `backend/app/agent_runtime/checkpointer.py:16`: defaults are `checkpointer_pool_min_size=1` and `checkpointer_pool_max_size=10` from `backend/app/config.py:17`. Evaluation concurrency must still be bounded because chat, builder, and evaluation share backend DB/checkpointer capacity.
- Local E2E can now seed a System LLM for `text_primary` through `seed_e2e_llm(...)` in `backend/app/main.py`; empty `E2E_LLM_*` values keep the normal missing-System-LLM path testable.
- `alembic heads` is the migration source of truth. On this implementation branch the current head is `m64_skill_builder_sessions`; if main advances again before the next migration, use the reported head instead of stale project guidance text.
- `git pull --ff-only origin main` on 2026-06-15 reported `Already up to date`; `origin/main` is `18838452` and is already an ancestor of this implementation branch. That merge includes the chat hardening commits `a0176b5a`, `93ea301a`, and `62c064df`.
- Merged `main` at `18838452` includes the LangGraph v3 chat runtime. `frontend/src/lib/chat/runtime-mode.ts:3` defaults to `langgraph_v3`; `legacy` is only selected when `NEXT_PUBLIC_CHAT_RUNTIME=legacy`.
- Normal chat now routes through `ChatRuntimeSection` and `useMoldyLangGraphStream(...)` in `frontend/src/components/chat/chat-runtime-section.tsx:88` and `:188`.
- `useMoldyLangGraphStream(...)` subscribes to `messages`, `tools`, `values`, `updates`, `lifecycle`, `tasks`, `checkpoints`, and `custom` channels in `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts:104`, then publishes activities, DeepAgents state, and subagent runtime into `AssistantThread`.
- Normal chat also hydrates `latest_run` / `active_run` state and appends stale or canceled terminal notices in `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts:121` and `:420`. Skill Builder must persist and display its own `skill_builder_sessions` / evaluation-run status instead of piggybacking on normal chat run notices.
- Normal Agent Protocol endpoints live under `/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/...` in `backend/app/routers/conversation_agent_protocol.py:64`, `:90`, `:137`, `:160`, and `:178`; `thread_id` must equal `conversation_id` through `get_owned_thread(...)` in `backend/app/routers/conversation_agent_protocol_runtime.py:31`.
- Agent Protocol SSE frames use `event: message` with payload `{type, method, params, seq, event_id}` through `format_protocol_sse(...)` in `backend/app/agent_runtime/protocol_events.py:196`. Custom domain events on that path must use `method="custom"` and data `{name, payload}` via `stored_custom_protocol_event(...)` in `backend/app/agent_runtime/protocol_events.py:113`.
- Agent Protocol streams set `X-Stream-Protocol` plus `X-Resume-Mode` / `X-Run-Id` headers through `protocol_headers(...)` in `backend/app/routers/conversation_agent_protocol_contracts.py:105`; `/stream/events` can serve `thread`, `live`, `replay`, or `stale` modes in `backend/app/routers/conversation_agent_protocol.py:198`. Skill Builder v1 should not depend on those normal-chat resume modes.
- Normal chat run lifecycle and recovery are backed by `ConversationRun` APIs: active run lookup at `backend/app/routers/conversation_runs.py:101`, run attach/replay/stale at `:121`, cancel at `:195`, and LangGraph SDK cancel compatibility at `:217`. Skill Builder sessions and skill evaluation runs need their own durable lifecycle rather than creating normal `ConversationRun` rows.
- Assistant panel resume is now explicit through `POST /api/agents/{agent_id}/assistant/message/resume` in `backend/app/routers/assistant.py:57` and `streamAssistantResume(...)` in `frontend/src/lib/sse/stream-assistant.ts:23`. Skill Builder can mirror the endpoint shape for approvals, but must keep its own router/service/thread id contract.
- Actual shared chat activity kinds/statuses are defined in `frontend/src/lib/chat/langgraph-runtime/activity-types.ts:1`. Skill Builder can keep builder-domain phases, but if it reuses shared activity UI it must map into those kinds/statuses rather than inventing new global activity kinds.
- Normal chat activity normalization first passes through `reduceProtocolActivity(...)` in `frontend/src/lib/chat/langgraph-runtime/activity-protocol.ts:241`, which adapts content-block, tool, state, and lifecycle events before calling the reducer. The reducer in `frontend/src/lib/chat/langgraph-runtime/activity-model.ts:219` still promotes only selected `custom` events (`artifact`, `file`, `file_event`, `memory*`, `stale`, `reconnect`) into shared activities. Skill Builder domain events will be ignored by that reducer unless a future Agent Protocol migration intentionally adds mappings and tests.
- Normal chat interrupt rendering uses `standardInterruptToToolCalls(...)` and `mergeInterruptToolCalls(...)` in `frontend/src/lib/chat/standard-interrupt.ts:126` and `:155`, with sensitive arguments redacted by `frontend/src/lib/chat/sensitive-display.ts:16`. The backend restores redacted edit placeholders on normal-chat resume through `restore_redacted_resume_payload(...)` in `backend/app/routers/conversation_agent_protocol_resume_redaction.py:18`. Skill Builder approval UI should preserve this display/redaction model only through a builder-owned resume path, because normal chat restoration depends on conversation thread checkpoints.
- Normal chat protocol streaming redacts sensitive keys in live frames through `redact_protocol_data(..., redact_memory=False)` in `backend/app/agent_runtime/langgraph_streaming.py:183`, while persisted protocol events pass through `persistable_protocol_event(...)` and the default memory redaction path in `backend/app/agent_runtime/protocol_persistence.py:15`. Skill Builder stream events and persisted session snapshots must apply their own secret-scan/redaction rules instead of assuming the normal chat protocol layer will cover builder payloads.
- The legacy W3-out `EventBroker` in `backend/app/agent_runtime/event_broker.py:81` is process-local with a ring buffer and live listener queues. Skill Builder may mirror the existing builder SSE helper pattern, but evaluation/build jobs that need durability must persist status in DB, not rely on a live broker.
- `docs/superpowers/plans/2026-06-13-assistant-ui-langgraph-v3-streaming.md` is now the committed source reference for the normal chat runtime. It intentionally keeps product builder surfaces on their own workflow streams until a dedicated Agent Protocol migration is planned.
- Frontend skill creation currently has three tabs in `frontend/src/components/skill/skill-create-dialog.tsx:16`. The `scratch` tab creates a minimal `.skill` package in browser with JSZip at `frontend/src/components/skill/skill-create-dialog.tsx:255`.
- `/skills` is a client page using `ResourcePage`, `ResourcePanel`, `CountedLineTabs`, `SearchInput`, `ResourceGrid`, and `ResourceListCard` in `frontend/src/app/skills/page.tsx:101`.
- The current skill card renders kind, slug, description, marketplace badges, version/agent count, publish action, and manage action in `frontend/src/app/skills/page.tsx:209`.
- `SkillDetailDialog` currently branches into separate `TextSkillEditor` and `PackageSkillEditor` render paths in `frontend/src/components/skill/skill-detail-dialog.tsx:108` and `:117`.
- Text skill detail currently shows credential bindings above a single textarea in `frontend/src/components/skill/skill-detail-dialog.tsx:180`.
- Package skill detail currently uses `DialogShell.Split`, `DialogShell.Sidebar`, `SkillPackageTree`, credential bindings, and `FileEditorPane` in `frontend/src/components/skill/skill-detail-dialog.tsx:466`.
- `SkillCredentialBindingsPanel` already returns `null` when the skill has no credential requirements in `frontend/src/components/skill/skill-detail-dialog.tsx:632`.
- `SkillPackageTree` is currently a simple always-expanded tree with file selection only in `frontend/src/components/skill/skill-package-tree.tsx:45`.
- Agent settings uses the same `Skill` list inside `ToolsSkillsDialog`; the skills panel currently renders only name, "Skill", description, and add action in `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog.tsx:511`.
- Frontend skill API and hooks are currently split through `frontend/src/lib/api/skills.ts:12` and `frontend/src/lib/hooks/use-skills.ts:14`.
- Frontend `Skill` type currently has no evaluation summary field in `frontend/src/lib/types/skill.ts:12`.
- Skill menu copy lives under `skill` in `frontend/messages/ko.json:2168` and `frontend/messages/en.json:2189`. English copy has several generic placeholder strings and should be tightened when adding new skill UI strings.
- Existing Builder frontend SSE helpers are `frontend/src/lib/sse/stream-builder-message.ts:9` and `frontend/src/lib/sse/stream-builder-resume.ts:11`. Treat them as product-workflow SSE patterns, not as evidence that normal chat uses legacy SSE.
- `backend/app/main.py` imports routers locally inside the app factory and includes routers near the existing `skills` router registration.

## Product Requirements

The feature is complete when:

- A user can start "대화로 만들기" from `/skills`.
- A user can open an existing skill and start "대화로 개선" from the skill detail dialog.
- The user sees a conversational UI, but the actual builder is not a normal Agent in the Agent list.
- The builder asks for missing intent only when needed:
  - what the skill should enable
  - when it should trigger
  - expected outputs
  - example prompts
  - whether scripts, references, assets, credentials, or tool dependencies are needed
- The builder creates a package preview with:
  - `SKILL.md`
  - optional `agents/openai.yaml`
  - optional `agents/moldy.yaml`
  - optional `scripts/`, `references/`, `assets/`
  - optional `evals/evals.json`, stored in session and excluded from exported `.skill` by default
- The preview shows validation issues before the user finalizes.
- Finalization creates a normal package-kind `Skill` row using the existing package storage path.
- Improvement finalization updates the existing owned `Skill` row only after human confirmation and conflict checks.
- Improvement sessions preserve the original skill snapshot, show a diff, and allow discard without changing the skill.
- The final skill can be opened in the existing Skill Detail dialog and edited with the existing package file editor.
- If credential requirements are generated, the existing credential binding panel shows them.
- Generated skills stay portable by default and do not put Moldy-only metadata in `SKILL.md` frontmatter.
- `/skills` promotes "대화로 만들기" as the default creation path while preserving text creation and package upload.
- Global navigation keeps a single `Skills` entry. Skill creation, credentials, evaluations, history, compatibility, and rollback live inside `/skills`, skill detail tabs, status filters, and deep links instead of separate top-level menus.
- Skill cards and agent skill picker rows show compact evaluation status so users can choose higher-quality installed skills.
- Skill detail uses a unified tabbed detail surface for content/files, credentials, evaluation, history, and metadata instead of mixing all controls into one editing surface; advanced tabs can be conditionally visible when they have content or an actionable state.
- Every installed skill can access evaluation details, reruns, and historical evidence from the skill detail dialog, while empty evaluation UI can stay hidden for simple skills until a run, health state, or deep link makes it relevant.
- Users can rerun an evaluation from an installed skill, not only from the initial builder session.
- Builder-time evals are copied to the finalized skill as reusable evaluation sets and historical runs.
- Evaluation templates are chosen automatically by the builder from skill intent and draft files. Users should not have to pick a preset in the default flow.
- Editing `SKILL.md`, `scripts/`, `references/`, or runtime metadata marks previous evaluation runs stale and prompts a one-click rerun from the skill detail view.
- Evaluation runs store evaluator versions so old scores remain explainable after grader prompts, schemas, or runner logic change.
- Evaluation reruns show a cost/time guard before execution and support cancellation or timeout status.
- Evaluation execution is backed by a bounded worker/queue, not only a synchronous request handler. Queued, running, grading, cancellation-requested, cancelled, failed, timed-out, and completed states must be durable.
- Skill cards and detail headers expose a compact Skill Health summary, not just raw evaluation scores.
- Generated and improved skills expose credential requirements through the existing skill credential APIs, and missing required user bindings set Skill Health to `needs_credentials`.
- Evaluation runs refuse to start when required credential bindings are missing; the UI opens the `Credentials` tab instead of asking users to choose eval presets or paste secrets.
- Builder, improvement, evaluation, sandbox denial, credential-missing, and secret-scan denial events are auditable without storing user prompts, generated output, full file content, or secret values in audit metadata.
- When `execute_in_skill` injects user-bound skill credentials, the run records credential-use audit rows through the existing credential audit service.
- Every skill content/package-changing operation creates an immutable skill revision snapshot so users can inspect history and roll back.
- Improve-mode confirmation shows a human-readable changelog before apply and stores that changelog with the resulting skill revision.
- Rollback restores an earlier revision through the same skill service/file APIs, creates a new revision for the rollback operation, and writes audit events.
- Validation includes a portable compatibility check for OpenAI/Codex-style skills, Claude Code-style skills, and Vercel Agent Skills-style portable packages.
- Changelog, rollback history, compatibility reports, evaluation results, and Moldy runtime metadata stay outside `SKILL.md`; `SKILL.md` remains concise and portable.
- Package skill `content_hash` changes whenever `SKILL.md`, `scripts/`, `references/`, `assets/`, or `agents/*.yaml` files change through upload, delete, builder confirm, rollback, or the package file editor.
- The conversational builder handles missing System LLM configuration gracefully with an actionable UI state and keeps text creation/package upload available.
- Existing skills that predate `skill_revisions` get a baseline history strategy: either a backfilled baseline revision or a clear empty-state that starts history from the next mutation.
- Credential requirements have one normalized source of truth. `agents/moldy.yaml` is authoring metadata, but installed skills use `skills.credential_requirements` and `skills.execution_profile`.
- Empty advanced sections are progressive. Simple skills should not force users through blank Credentials, Evaluation, History, or Metadata panels unless a status, action, or deep link makes the panel relevant.

## Non-Goals

- Do not replace the existing text skill editor.
- Do not replace package upload.
- Do not silently mutate existing skills from a chat response. Improvements require an explicit apply/confirm action.
- Do not add a new marketplace publishing flow in this feature. Existing publish-from-skill remains the publish path.
- Do not add separate top-level navigation items for skill credentials, skill evaluations, skill history, rollback, or compatibility reports in this phase.
- Do not expose the Skill Builder as a selectable normal Agent.
- Do not require users to understand JSON, evals, or benchmark terminology in the main UX.
- Do not make users choose evaluation presets during normal skill creation. Template selection is automatic, with optional advanced review only.
- Do not audit read-only skill views, requirement list reads, binding list reads, or opening the Credentials tab; audit mutations, execution, denials, and lifecycle transitions instead.
- Do not store user prompt text, generated answer text, full file bodies, stdout/stderr, credential field values, or raw command arguments in audit metadata.
- Do not allow user skills, the hidden builder, or evaluation runs to bind operator-managed system credentials through the user credential binding flow.
- Do not append changelogs, evaluation reports, revision history, or platform compatibility reports to `SKILL.md`.
- Do not make rollback edit old revision rows. Rollback creates a new current revision that restores an older snapshot.

## Architecture Decision

Use **LangGraph** for the hidden builder workflow.

Reason:

- The builder needs explicit stages: collect intent, draft files, validate, ask for feedback, revise, optionally evaluate, then confirm.
- The product needs deterministic session state and preview persistence.
- Deep Agents are still used by Moldy for normal agent runtime and can be used later for eval execution, but the builder's outer flow should own graph transitions directly.

Use existing system LLM selection:

- Resolve the hidden builder model with `resolve_system_model(db, "text_primary")`, same as Assistant.
- Construct it with `create_chat_model(...)`.
- If a dedicated system role is later wanted, add it as a separate follow-up migration after this plan ships.

## Execution Readiness Gate

Before starting implementation from this document, run these checks and update the plan if any result differs:

```bash
git status -sb
git pull --ff-only origin main
cd backend && uv run alembic heads
cd ../frontend && pnpm lint:i18n
```

Required state:

- `main` is pulled to a commit that includes the LangGraph v3 chat runtime (`18838452` or newer in this review). This implementation branch currently has no upstream, so use `git pull --ff-only origin main` rather than plain `git pull` unless an upstream is configured.
- `alembic heads` returns the current project head. On this branch Task 1 already added `m64_skill_builder_sessions`; any later migration must use that reported head, or whatever newer head exists after the next main pull.
- `backend/.env` is linked or copied through `bash scripts/worktree-setup.sh` for this worktree, so credential encryption keys and System LLM seed behavior match local development.
- Existing unrelated modified files are left alone. This plan currently expects new work to happen on a feature branch/worktree and not to revert user edits.

Implementation order:

1. Tasks 1-5 create the durable backend product surface.
2. Tasks 6-9 add authoring intelligence and evaluation quality loops.
3. Tasks 10-12 can start after the current `main` pull/rebase because they must align with the merged LangGraph v3 chat runtime assumptions.
4. Tasks 13-15 are required before calling the feature done.

## LangGraph v3 Chat Runtime Compatibility

`main` now includes the LangGraph v3 normal chat runtime. Skill Builder implementation must treat these as current source facts, not pending branch assumptions:

- `NEXT_PUBLIC_CHAT_RUNTIME` unset means normal agent chat uses `langgraph_v3`; `legacy` becomes the explicit rollback mode.
- Normal conversations use `ChatRuntimeSection`, `useMoldyLangGraphStream(...)`, and Agent Protocol thread/state/stream endpoints rather than the old message-only SSE path.
- Normal chat transport calls `/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/commands`, `/state`, and `/stream/events`; the current backend requires `thread_id == conversation_id`.
- Normal Agent Protocol stream events are sent as SSE `event: message` frames whose data contains `method`, `params`, `seq`, and `event_id`. Builder-specific SSE event names such as `builder_status` must not be sent through that normal chat endpoint.
- Normal chat has a separate `ConversationRun` recovery surface for active run lookup, live attach, replay, stale marking, and cancellation. Skill Builder must not assume that opening the builder dialog will recover through `/api/conversations/{conversation_id}/runs/active`, `/runs/{run_id}/stream`, or `/threads/{thread_id}/runs/{run_id}/cancel`.
- The shared activity UI recognizes `thinking`, `planning`, `tool`, `subagent`, `background_subagent`, `artifact`, `memory`, `interrupt`, `checkpoint`, `responding`, `reconnecting`, `done`, and `error`, with statuses `pending`, `running`, `requires_action`, `complete`, `error`, and `cancelled`.
- The current activity pipeline normalizes protocol events before reducing them into UI activities, but it still converts `custom` protocol events into shared activities only for artifact/file, memory, stale, and reconnect events. A future builder-on-Agent-Protocol migration must add explicit reducer mappings and tests before expecting `builder_status`, `builder_activity`, validation, compatibility, or evaluation events to appear in the normal chat activity rail.
- Normal chat standard interrupts are converted into redacted synthetic tool calls, and redacted edit placeholders are restored server-side from normal conversation checkpoints on resume. Skill Builder can keep a builder-specific approval surface in v1, but any reuse of the standard interrupt payload must keep sensitive argument redaction, multi-action decision coordination, and a builder-owned restoration path for redacted edits.
- Normal chat redaction is split by surface: live protocol frames redact sensitive keys, persisted protocol events also redact memory content, and frontend approval cards redact displayed tool arguments. Skill Builder streams, snapshots, changelogs, audit metadata, and eval artifacts must not rely on the normal chat protocol redactor; apply the Skill Builder secret scan/redaction rules at each builder-owned persistence and stream boundary.

Skill Builder v1 should remain a separate authoring workflow:

- `skill_builder_sessions` is the canonical session state, not `conversation_runs`.
- The builder is not mounted through `conversation_agent_protocol` and does not create normal Agent Protocol runs.
- `SkillBuilderDialog` should not import or wrap `ChatRuntimeSection`; it can share low-level SSE parse/resume utilities but owns its own UX, phases, preview, validation, and confirm/apply state.
- If Skill Builder needs approval/resume semantics, implement builder-owned `/api/skill-builder/{session_id}/messages/resume` behavior and tests. Do not reuse Assistant panel `/assistant/message/resume` or normal chat HITL routes, because their thread IDs, ownership model, and run lifecycle are different.
- Builder stream payloads should align with shared status names where possible. Builder-domain stages such as `validation`, `compatibility`, `evaluation`, and `revision` should live in `phase`, `label`, or `data.domain`, not in shared `RunActivityKind`, unless a dedicated shared-kind addition is implemented.
- If a later product direction embeds Skill Builder inside normal chat, add a dedicated migration task to emit Agent Protocol `updates` or `custom` events. On the protocol path, use `method="custom"` with `{name, payload}` rather than standalone SSE event names.

Testing implication:

- Frontend tests and manual E2E must run with the default app shell where `NEXT_PUBLIC_CHAT_RUNTIME` is unset and therefore `langgraph_v3` is active.
- A small rollback smoke test may also run with `NEXT_PUBLIC_CHAT_RUNTIME=legacy`, but Skill Builder must not depend on legacy normal-chat runtime internals.

## Deep Agents Alignment

LangChain's framework guidance points to **Deep Agents** when work requires planning, file management, subagents, memory, or on-demand skills. Skill creation and skill improvement have those properties, but Moldy also needs product-owned session state, authorization, preview, evaluation records, and explicit final confirmation.

Recommended architecture:

- Keep **LangGraph** as the outer product workflow:
  - session lifecycle
  - create vs improve mode
  - validation gates
  - human confirmation
  - DB writes
  - SSE events
  - idempotent confirm/apply behavior
- Use a **Deep Agent draft worker** inside graph nodes when generating or revising multi-file skill packages:
  - TodoListMiddleware helps plan the package/revision.
  - Filesystem tools work against draft storage, not unrestricted production disk.
  - SkillsMiddleware can load Codex/Claude-style skill-creator guidance as internal instructions.
  - SubAgentMiddleware can isolate grader, analyzer, and comparator roles during evaluation.
  - HumanInTheLoopMiddleware is useful for proposed file writes, but Moldy's product confirm screen remains the final approval.

Safety constraints for Moldy:

- Do not use unrestricted `FilesystemBackend` in the web server. Draft files must live in `skill_builder_sessions.draft_package`, temp dirs, or a sandboxed Store/State backend.
- Do not let a Deep Agent write directly to `data/skills/<id>` for installed skills. Final writes must go through `skill_builder_service.confirm_session` and existing `skill_service` file APIs.
- Use a stable `thread_id = f"skill_builder_{session_id}"` for conversation continuity.
- If custom subagents are used for grading or analysis, pass any required skills explicitly; custom subagents do not inherit skills automatically.
- Subagent calls must receive complete instructions because subagents are stateless between calls.

Deep Agents are therefore recommended for the **authoring worker**, not as a replacement for Moldy's persisted builder session and router/service layer.

## Credential-Aware Sandbox And Audit Policy

This feature must preserve the current Moldy credential and audit model instead of creating a parallel one.

### Credential Requirements

Use the existing `Skill.credential_requirements` JSON column as the source of truth for installed skills.

Builder and improvement rules:

- The hidden builder may propose credential requirements in `agents/moldy.yaml`.
- Validation must reject malformed requirement entries before confirm:
  - missing `key`, `definition_key`, `required`, or `label`
  - `definition_key` not present in `app.credentials.registry`
  - `injection` outside `env` or `config`
  - `scope` outside `user`, `system_dependency`, or `manual`
  - `env_map` using the wrong direction. The runtime expects `{credential_field_name: env_var_name}`, matching `backend/app/marketplace/k_skill_requirements.py:50`.
  - `env_map` references a credential field not declared in `fields`
  - env var names not matching `^[A-Z_][A-Z0-9_]*$`
- Confirm stores validated requirements on `skill.credential_requirements`.
- Improvement confirm replaces the skill's credential requirements only after the same conflict check used for file/content changes.
- The user never pastes secret values into the builder chat. The builder asks whether a credential type is needed and generates metadata only.

Installed-skill UI rules:

- Reuse `GET /api/skills/{skill_id}/credential-requirements`.
- Reuse `GET/PUT/DELETE /api/skills/{skill_id}/credential-bindings`.
- Move `SkillCredentialBindingsPanel` into the new `Credentials` tab.
- If required user-scope bindings are missing, Skill Health returns `needs_credentials`, skill cards show `자격증명 필요`, and Evaluation tab primary action changes from `평가 실행` to `자격증명 연결`.
- Clicking `자격증명 연결` opens the `Credentials` tab and focuses the first missing required requirement.
- Do not add another credential picker to the eval flow.

Runtime and evaluation rules:

- Before chat/runtime execution, keep using `resolve_runtime_credentials(...)`; it raises `marketplace_credential_required` for missing required user bindings.
- Before installed-skill evaluation run creation, call the same requirement/binding resolution path or `missing_required_keys(...)`; if missing, create no run row and return a structured `MARKETPLACE_CREDENTIAL_REQUIRED` error.
- Inject only fields declared by `env_map`. Never inject the full decrypted credential JSON.
- Redact stdout, stderr, eval evidence summaries, streamed tool results, and audit metadata with the existing redaction helpers.

### Sandbox Execution

The builder and evaluation runner must not create a weaker execution surface than the current runtime.

Rules:

- Deep Agent file tools, if enabled, operate only on draft storage or a temporary directory for the current `skill_builder_session`.
- The hidden builder never writes directly to `data/skills/<id>`.
- Confirm writes through `skill_builder_service.confirm_session(...)` and existing `skill_service` package/file APIs.
- Draft validation materializes package files into `tempfile.TemporaryDirectory()` and runs the existing `scan_package(...)` secret scanner.
- Evaluation execution must use the same selected-skill runtime mount pattern as `build_skill_runtime_context(...)`: a per-thread copy under `data/runtime/<thread_id>/.../skills/<slug>`.
- Script execution must go through the same command policy as `execute_in_skill`:
  - no shell
  - allowed executables are `python`, `node`, and `curl`
  - Python and Node script paths must resolve inside the skill directory
  - default timeout is 30 seconds
  - maximum timeout is 420 seconds
  - `HOME`, `PYTHONPATH`, `SKILL_OUTPUT_DIR`, and `OUTPUTS_DIR` are scoped to the skill/runtime output locations
- Refactor shared command parsing/timeout/redaction into a reusable helper if evaluation needs to call scripts outside the LangChain tool wrapper. Do not duplicate a looser parser in `eval_runner.py`.
- If a generated or improved skill uses `curl`, network APIs, or hosted search, require explicit metadata in `execution_profile`:
  - `requires_network: true`
  - optional `tool_dependencies`, currently limited by `SUPPORTED_SKILL_TOOL_DEPENDENCIES`
  - matching `credential_requirements` when the API needs user-provided secrets
- Validation warns when network-like commands appear without `requires_network: true`; evaluation refuses network execution unless this metadata is present.

### Audit Events

There are two audit streams:

- `audit_events`: user-visible lifecycle, mutation, security decision, denial, and run status events.
- `credential_audit_logs`: credential lifecycle/use events. `credential_service.write_audit_log(...)` also writes a global `credential.<action>` audit event.

Use `audit_service.record_event(...)` for builder/evaluation lifecycle events and `credential_service.write_audit_log(...)` for actual user credential injection/use.

Audit action matrix:

| Event                                 | Target type                        | Outcome   | Metadata allowed                                                             |
| ------------------------------------- | ---------------------------------- | --------- | ---------------------------------------------------------------------------- |
| `skill_builder.session_create`        | `skill_builder_session`            | `success` | `mode`, `source_skill_id`, `source_skill_hash`, `session_id`                 |
| `skill_builder.validation_failed`     | `skill_builder_session`            | `failure` | issue codes, severities, paths, counts                                       |
| `skill_builder.secret_scan_blocked`   | `skill_builder_session`            | `denied`  | finding count, finding kinds, paths only                                     |
| `skill_builder.compatibility_checked` | `skill_builder_session`            | `success` | target names, error/warning/info counts                                      |
| `skill_builder.changelog_generated`   | `skill_builder_session`            | `success` | changed file count, item count, risk note count                              |
| `skill_builder.confirm_create`        | `skill`                            | `success` | session id, skill id, file count, content hash, credential requirement count |
| `skill_builder.apply_improvement`     | `skill`                            | `success` | session id, changed file count, added/deleted counts, old hash, new hash     |
| `skill_builder.apply_conflict`        | `skill`                            | `denied`  | session id, base hash, current hash                                          |
| `skill_revision.create`               | `skill_revision`                   | `success` | skill id, revision number, operation, content hash, file count               |
| `skill_revision.rollback`             | `skill`                            | `success` | skill id, restored revision id, new revision id, old hash, new hash          |
| `skill_evaluation.run_create`         | `skill_evaluation_run`             | `success` | skill id, set id, case count, estimate summary, skill content hash           |
| `skill_evaluation.run_start`          | `skill_evaluation_run`             | `success` | runner version, evaluator versions                                           |
| `skill_evaluation.run_complete`       | `skill_evaluation_run`             | `success` | pass rate, passed, failed, total, duration, token counts                     |
| `skill_evaluation.run_fail`           | `skill_evaluation_run`             | `failure` | error code, phase, duration                                                  |
| `skill_evaluation.run_cancel`         | `skill_evaluation_run`             | `success` | previous status, cancelled phase                                             |
| `skill_evaluation.credential_missing` | `skill`                            | `denied`  | missing requirement keys, set id if applicable                               |
| `skill_security.sandbox_denied`       | `skill` or `skill_builder_session` | `denied`  | reason code, command executable, skill id, session/run id                    |

Audit metadata must not include:

- user prompt text
- assistant answer text
- full `SKILL.md` body
- file contents
- stdout or stderr
- raw command arguments
- credential field values
- decrypted credential objects
- API keys, bearer tokens, passwords, OAuth tokens, private keys, `.env` contents

For skill credential injection:

- Extend `SkillToolContext` with enough non-secret correlation fields to audit execution: `user_id`, `agent_id`, `thread_id`, and optional `run_id`.
- In `execute_in_skill`, after command validation and before subprocess launch, write one best-effort credential audit row per unique injected credential:
  - `action="invoke"`
  - `source="runtime"`
  - metadata: `kind="skill_runtime"`, `skill_id`, `skill_slug`, `requirement_key`, `agent_id`, `thread_id`, `command_executable`, `timeout_seconds`
- In installed-skill evaluation runs, write equivalent credential audit rows with metadata `kind="skill_evaluation"` and `run_id`.
- Do not audit a credential requirement merely being displayed or listed.

## Portable Skill Contract

Generated packages use this structure:

```text
<skill-name>/
├── SKILL.md
├── agents/
│   ├── openai.yaml
│   └── moldy.yaml
├── scripts/
├── references/
├── assets/
└── evals/
    └── evals.json
```

Required:

- `SKILL.md`
- frontmatter `name`
- frontmatter `description`
- Markdown instructions body

Generated by default:

- `agents/openai.yaml` with UI metadata that matches Codex conventions:
  - `interface.display_name`
  - `interface.short_description`
  - `interface.default_prompt`
  - `policy.allow_implicit_invocation`
- `agents/moldy.yaml` for Moldy-specific metadata:
  - `version`
  - `credential_requirements`
  - `execution_profile`
  - `eval_policy`

Portability rule:

- `SKILL.md` frontmatter should contain only cross-platform fields. Use `name` and `description` for new builder output. Store Moldy-specific data in DB and `agents/moldy.yaml`.
- Existing imports with `version`, `license`, `allowed-tools`, `metadata`, or `compatibility` continue to be accepted where current validators already allow them.
- `SKILL.md` is not a changelog, eval report, rollback record, or Moldy settings dump. Keep it focused on trigger conditions, instructions, and references.
- Store revision history, generated changelogs, compatibility check results, evaluation summaries, credential bindings, and runtime policy in DB tables or `agents/moldy.yaml`.
- Exported `.skill` packages exclude `evals/` and revision history by default. A later advanced export option may include `CHANGELOG.md`, but the normal portable package should stay small.

### Portable Compatibility Check

Validation produces `compatibility_result` in addition to normal validation issues.

Compatibility targets:

- `openai_codex`: `SKILL.md` plus optional `agents/openai.yaml`; minimal frontmatter; progressive disclosure through referenced files; no Moldy-only frontmatter.
- `claude_code`: concise description with trigger conditions; referenced `scripts/`, `references/`, and `assets/` paths exist; no hidden dependency on Moldy DB state.
- `vercel_agent_skills`: package remains a modular skill directory that can be installed by a skills CLI or directory-style installer. Vercel's docs describe agent skills as packaged capabilities and note broad agent compatibility, so Moldy output should avoid runtime-specific assumptions in the portable layer.

UX wording:

- In the default UI, label the aggregate as `Portable agent skill compatibility`.
- Show OpenAI/Codex and Claude Code as primary compatibility targets.
- Show Vercel Agent Skills as an advisory/advanced target unless the user asks for that target explicitly.
- Vercel-specific warnings should not block finalization unless the same package also violates a generic portable skill rule.

Compatibility checks:

- Error when `SKILL.md` contains Moldy-only frontmatter keys: `credential_requirements`, `execution_profile`, `eval_policy`, `skill_builder_session_id`, `moldy`, `rollback`, `revision_history`.
- Warning when `agents/openai.yaml` is missing for a generated skill.
- Warning when `agents/openai.yaml.interface.default_prompt` does not reference `$<skill-name>`.
- Warning when `agents/moldy.yaml` is missing but the skill has credential requirements, execution profile, or eval policy.
- Warning when `SKILL.md` has absolute local paths, localhost URLs, or references to `data/skills/`, `backend/`, `.env`, or Moldy API routes.
- Warning when `scripts/` exist but `SKILL.md` does not explain when to run them.
- Warning when `references/` exist but no reference file is linked from `SKILL.md`.
- Warning when `SKILL.md` is over 500 lines or repeats generated changelog/eval text.
- Info when optional `evals/evals.json` exists; it is useful for Moldy quality checks but excluded from default portable export.

### Credential Requirement Source Of Truth

The builder can author credential and execution metadata in three places during a session:

- `agents/moldy.yaml` inside the draft package for portable package metadata.
- `SkillDraftPackage.credential_requirements` and `SkillDraftPackage.execution_profile` in session JSON.
- `skills.credential_requirements` and `skills.execution_profile` on the installed skill row.

Source-of-truth rules:

- During draft authoring, `agents/moldy.yaml` is treated as user-visible package metadata, not as the installed runtime source of truth.
- Validation parses `agents/moldy.yaml`, normalizes it, and writes the normalized values to `SkillDraftPackage.credential_requirements` and `SkillDraftPackage.execution_profile`.
- If the normalized session fields and `agents/moldy.yaml` disagree after validation, validation returns an error with code `MOLDY_METADATA_DIVERGENCE`.
- `confirm_session(...)` writes only the normalized session fields to the installed `Skill` row.
- Runtime, Skill Health, credential binding UI, marketplace install checks, and evaluation runner read from the installed `Skill` row.
- `agents/moldy.yaml` is kept in the package as a portable metadata snapshot for export/import, but it is not reparsed at runtime on every execution.

Validator behavior:

- Accept `agents/moldy.yaml` as the authoring surface for credential requirements and execution profile.
- Reject unknown credential definition keys, reversed `env_map`, invalid env var names, and fields not listed in the requirement.
- Warn when a draft contains credential-like instructions in `SKILL.md` but no normalized `credential_requirements`.
- Warn when normalized credential requirements exist but the draft package has no `agents/moldy.yaml`; generated portable packages should include it for round-trip clarity.

## Data Model

Create `SkillBuilderSession` instead of overloading `BuilderSession`.

Skill evaluations must be first-class records linked to installed `Skill` rows. Do not keep evaluation results only inside `skill_builder_sessions`; sessions are authoring history, while `skill_evaluation_*` tables are the durable quality record for each installed skill.

### New Status Enum

Create `backend/app/schemas/skill_builder.py`.

```python
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillBuilderStatus(enum.StrEnum):
    COLLECTING = "collecting"
    DRAFTING = "drafting"
    VALIDATING = "validating"
    REVIEW = "review"
    EVALUATING = "evaluating"
    OPTIMIZING = "optimizing"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillBuilderMode(enum.StrEnum):
    CREATE = "create"
    IMPROVE = "improve"


class SkillBuilderStartRequest(BaseModel):
    user_request: str = Field(..., min_length=1, max_length=4000)
    mode: SkillBuilderMode = SkillBuilderMode.CREATE
    source_skill_id: uuid.UUID | None = None


class SkillBuilderMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class SkillBuilderResumeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    decisions: list[dict[str, Any]] = Field(..., min_length=1)
    display_text: str | None = Field(default=None, max_length=200)
    interrupt_id: str | None = Field(default=None, max_length=200)


class SkillDraftFile(BaseModel):
    path: str
    content: str
    media_type: str = "text/plain"
    role: Literal["skill", "script", "reference", "asset", "metadata", "eval"] = "skill"


class SkillValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    path: str | None = None
    message: str


class SkillDraftPackage(BaseModel):
    name: str
    slug: str
    description: str
    files: list[SkillDraftFile] = Field(default_factory=list)
    credential_requirements: list[dict[str, Any]] = Field(default_factory=list)
    execution_profile: dict[str, Any] = Field(default_factory=dict)
    validation_issues: list[SkillValidationIssue] = Field(default_factory=list)
    compatibility_result: dict[str, Any] | None = None
    changelog_draft: dict[str, Any] | None = None
    evals: dict[str, Any] | None = None
    benchmark: dict[str, Any] | None = None


class SkillBuilderSessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_request: str
    mode: SkillBuilderMode
    source_skill_id: uuid.UUID | None = None
    base_skill_version: str | None = None
    base_content_hash: str | None = None
    status: SkillBuilderStatus
    current_phase: int
    messages: list[dict[str, Any]] | None = None
    intent: dict[str, Any] | None = None
    draft_package: SkillDraftPackage | None = None
    validation_result: dict[str, Any] | None = None
    compatibility_result: dict[str, Any] | None = None
    changelog_draft: dict[str, Any] | None = None
    finalized_skill_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

### New ORM Model

Create `backend/app/models/skill_builder_session.py`.

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus


class SkillBuilderSession(Base):
    __tablename__ = "skill_builder_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_request: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(
        String(20),
        default=SkillBuilderMode.CREATE.value,
        server_default=SkillBuilderMode.CREATE.value,
        nullable=False,
    )
    source_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default=SkillBuilderStatus.COLLECTING,
        nullable=False,
    )
    current_phase: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    messages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    intent: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft_package: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    compatibility_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    changelog_draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    eval_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trigger_eval_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    finalized_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    user: Mapped["User"] = relationship()
    source_skill: Mapped["Skill | None"] = relationship(foreign_keys=[source_skill_id])
    finalized_skill: Mapped["Skill | None"] = relationship(foreign_keys=[finalized_skill_id])
```

### Migration

Create `backend/alembic/versions/m64_skill_builder_sessions.py`.

```python
"""M64: conversational skill builder sessions."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m64_skill_builder_sessions"
down_revision = "m63_chat_navigator_indexes"  # Replace with `uv run alembic heads` output if main has advanced.
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_builder_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="create"),
        sa.Column("source_skill_id", sa.Uuid(), nullable=True),
        sa.Column("base_skill_version", sa.String(length=40), nullable=True),
        sa.Column("base_content_hash", sa.String(length=64), nullable=True),
        sa.Column("base_snapshot", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="collecting"),
        sa.Column("current_phase", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages", sa.JSON(), nullable=True),
        sa.Column("intent", sa.JSON(), nullable=True),
        sa.Column("draft_package", sa.JSON(), nullable=True),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("compatibility_result", sa.JSON(), nullable=True),
        sa.Column("changelog_draft", sa.JSON(), nullable=True),
        sa.Column("eval_result", sa.JSON(), nullable=True),
        sa.Column("trigger_eval_result", sa.JSON(), nullable=True),
        sa.Column("finalized_skill_id", sa.Uuid(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["finalized_skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_builder_sessions_user_updated",
        "skill_builder_sessions",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "ix_skill_builder_sessions_finalized_skill",
        "skill_builder_sessions",
        ["finalized_skill_id"],
    )
    op.create_index(
        "ix_skill_builder_sessions_source_skill",
        "skill_builder_sessions",
        ["source_skill_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_builder_sessions_source_skill", table_name="skill_builder_sessions")
    op.drop_index("ix_skill_builder_sessions_finalized_skill", table_name="skill_builder_sessions")
    op.drop_index("ix_skill_builder_sessions_user_updated", table_name="skill_builder_sessions")
    op.drop_table("skill_builder_sessions")
```

Also modify:

- `backend/app/models/__init__.py`: import/export `SkillBuilderSession`.
- If user deletion needs explicit cleanup beyond cascade, update `backend/app/services/user_service.py` only if tests show the cascade is not enough.

### Persistent Skill Evaluation Models

Create `SkillEvaluationSet` and `SkillEvaluationRun`.

Why:

- A skill may be created by the builder, uploaded as `.skill`, installed from marketplace, or edited later.
- Users should see evaluation history from the installed skill detail view.
- Reruns must compare the current skill content against the same test set and keep historical snapshots.

Create `backend/app/models/skill_evaluation.py`.

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SkillEvaluationSet(Base):
    __tablename__ = "skill_evaluation_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="builder",
        server_default="builder",
    )
    template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    generation_strategy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evals: Mapped[list] = mapped_column(JSON, nullable=False)
    expectations_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    skill: Mapped["Skill"] = relationship()


class SkillEvaluationRun(Base):
    __tablename__ = "skill_evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluation_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_evaluation_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    skill_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runner_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runner_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1", server_default="1")
    grader_prompt_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1",
        server_default="1",
    )
    eval_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    run_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    estimate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    benchmark: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    skill: Mapped["Skill"] = relationship()
    evaluation_set: Mapped["SkillEvaluationSet"] = relationship()
```

Add these tables to `backend/alembic/versions/m64_skill_builder_sessions.py` in the same migration as the builder session. They are part of the same product slice because builder-time evals must persist onto finalized skills.

Indexes:

- `ix_skill_evaluation_sets_skill_updated` on `["skill_id", "updated_at"]`
- `ix_skill_evaluation_runs_skill_created` on `["skill_id", "created_at"]`
- `ix_skill_evaluation_runs_set_created` on `["evaluation_set_id", "created_at"]`
- `ix_skill_evaluation_runs_status` on `["status"]`

Also modify:

- `backend/app/models/__init__.py`: import/export `SkillEvaluationSet` and `SkillEvaluationRun`.

Run status rules:

- `queued`: row was created and committed, but the worker has not started execution.
- `running`: worker is running with-skill or baseline cases.
- `grading`: worker finished raw executions and is grading/aggregating evidence.
- `completed`: summary, benchmark, and case results are durable.
- `failed`: worker hit a validation, runtime, grader, or timeout error. Timeout errors begin with `timeout:`.
- `cancelled`: user cancellation was accepted. Queued runs can move directly to `cancelled`; running/grading runs set `cancellation_requested_at` first and the worker cooperatively stops at the next safe checkpoint.

### Skill Revision History

Skill revisions follow the same product pattern as `ConversationArtifact.current_version_id` plus immutable `ArtifactVersion` rows:

- the skill row points at the current revision
- each revision has a monotonically increasing `revision_number`
- revision rows are immutable
- rollback creates a new revision whose snapshot matches an older revision

Modify `backend/app/models/skill.py`:

```python
current_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
```

Create `backend/app/models/skill_revision.py`.

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SkillRevision(Base):
    __tablename__ = "skill_revisions"
    __table_args__ = (
        UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"),
        Index("ix_skill_revisions_skill_created", "skill_id", "created_at"),
        Index("ix_skill_revisions_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_builder_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    restored_from_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    object_key: Mapped[str] = mapped_column(String(800), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files: Mapped[list | None] = mapped_column(JSON, nullable=True)
    changelog_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    changelog_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    compatibility_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evaluation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    skill: Mapped["Skill"] = relationship()
```

Revision operations:

- `create`: initial revision created when a skill is created or uploaded.
- `manual_metadata_update`: metadata-only change.
- `manual_content_update`: text skill content change.
- `manual_file_update`: package file create/update/delete/upload.
- `builder_create`: skill created from a builder session.
- `builder_improvement`: existing skill changed by "대화로 개선".
- `rollback`: current skill restored from an older revision.

Storage:

- Store each revision snapshot as a zip under `data/skill-revisions/<skill_id>/r<revision_number>/skill.zip`.
- Text skills snapshot as a zip with one `SKILL.md` file.
- Package skills snapshot as a zip of the package directory.
- Do not include credential values, eval run artifacts, or builder chat transcripts in revision zip files.
- Store changelog and compatibility result in the revision row, not inside `SKILL.md`.

Rollback behavior:

- `rollback_to_revision(skill_id, revision_id)` loads the selected immutable snapshot.
- It verifies `skill.user_id == user.id`.
- It restores files through `skill_service.update_text_content(...)` or package file replacement helpers.
- It recalculates `content_hash`, `size_bytes`, `package_metadata`, `credential_requirements`, and `execution_profile`.
- It creates a new `SkillRevision` with `operation="rollback"` and `restored_from_revision_id=<selected revision id>`.
- It updates `skills.current_revision_id` to the new rollback revision.
- It does not delete or modify the older revision.

### Package Skill Content Hash Invariant

`skill.content_hash` is the conflict, stale-evaluation, health, and revision comparison key. It must change whenever the effective package changes.

Implemented baseline:

- `create_text_skill(...)` and `update_text_content(...)` already update `content_hash`.
- `create_package_skill(...)` now stores a deterministic package tree hash.
- `set_skill_file(...)` and `delete_skill_file(...)` now refresh package metadata and recalculate `skill.content_hash`.

Implemented `backend/app/skills/package_hash.py`:

```python
from __future__ import annotations

from pathlib import Path


def compute_package_tree_hash(root: Path) -> str:
    """Return a 64-character sha256 hex digest for a deterministic package tree snapshot."""
```

Hash rules:

- Traverse all regular files under the package root in sorted POSIX path order.
- Include each relative POSIX path, file size, and file sha256 digest in the tree hash.
- Exclude `.DS_Store`, transient editor swap files, and Moldy runtime output folders if they ever appear inside a package directory.
- Do not follow symlinks; package extraction already rejects them, and hash computation should fail closed if one is found.
- Return the same bare 64-character hex digest used by existing `Skill.content_hash`, `MarketplaceVersion.content_hash`, and current skill tests. Do not prefix with `sha256:` because the current DB columns are `String(64)` and existing package/text hashes are unprefixed.

Remaining service integration rules:

- Package file create/update/delete and package upload already pass through a path that refreshes metadata and content hash.
- Ensure builder confirm, whole-package replacement, and rollback pass through the same package write/refresh path.
- Include the new content hash in `SkillRevision.content_hash`, evaluation run snapshots, Skill Health, and improve-mode conflict checks.
- Do not update old evaluation rows when content changes. Staleness is always `run.skill_content_hash != skill.content_hash`.

Required tests:

- Updating `scripts/foo.py` through `set_skill_file(...)` changes `skill.content_hash`. Covered by `backend/tests/test_skill_package_hash_integration.py`.
- Deleting a file through `delete_skill_file(...)` changes `skill.content_hash`. Covered by `backend/tests/test_skill_package_hash_integration.py`.
- Rewriting a file to identical bytes keeps the same `content_hash`. Covered by `backend/tests/test_skill_package_hash_integration.py`.
- Updating `SKILL.md` through `set_skill_file(...)` changes `skill.content_hash`.
- Package file mutation creates a revision with the new hash.
- A completed evaluation run becomes stale after a package file mutation by hash comparison only.

### Skill Revision Retention And Backfill

Revision history should make improvement safe without creating unbounded local storage.

Defaults:

- Keep at least the latest 20 revisions per skill.
- Keep all revisions from the last 180 days.
- Never prune the current revision, marketplace-published revision markers, or a revision referenced by an active builder improvement session.
- Pruning deletes revision zip objects and marks the row with a metadata flag only if the UI can still explain that the binary snapshot was pruned. Prefer keeping the row for auditability.

Create `backend/scripts/backfill_skill_revisions.py`:

- Iterates user-owned and system skills in batches.
- Skips skills that already have at least one revision.
- Creates `operation="create"` baseline revisions from the current skill files.
- Stores `metadata_json.backfilled = true`.
- Can be run manually after deployment and safely rerun.

UI behavior for legacy skills:

- If no revision exists, the History tab shows: `현재 버전부터 이력이 쌓입니다.`
- On the first mutation of a skill with no revision, create a baseline `create` revision before applying the mutation, then create the mutation revision.
- The empty History tab should not make users think rollback is broken; it should explain that old snapshots were not available before this feature.

## API Contract

Create `backend/app/routers/skill_builder.py` with prefix `/api/skill-builder`.

Endpoints:

```text
POST /api/skill-builder
GET  /api/skill-builder/{session_id}
POST /api/skill-builder/{session_id}/messages
POST /api/skill-builder/{session_id}/messages/resume
POST /api/skill-builder/{session_id}/validate
POST /api/skill-builder/{session_id}/confirm
POST /api/skill-builder/{session_id}/evals/run
POST /api/skill-builder/{session_id}/trigger-evals/run
```

`POST /api/skill-builder` supports two modes:

```json
{
  "mode": "create",
  "user_request": "회의록 액션 아이템 추출 스킬을 만들어줘"
}
```

```json
{
  "mode": "improve",
  "source_skill_id": "uuid",
  "user_request": "이 스킬이 마감일을 더 정확하게 뽑도록 개선해줘"
}
```

Create-mode behavior:

- Starts with an empty draft package.
- Final confirm creates a new package-kind `Skill` row.

Improve-mode behavior:

- Loads the owned `source_skill_id` through `skill_service.get_skill(db, source_skill_id, user.id)`.
- Materializes the current text or package skill into `session.base_snapshot`.
- Stores `base_skill_version` and `base_content_hash`.
- Seeds `draft_package` from the existing skill files.
- The chat agent proposes edits as a full draft package plus a file-level diff summary.
- The chat agent generates a concise changelog draft with `summary`, `items`, and `risk_notes`.
- The review screen shows the changelog draft before the user applies changes.
- Final confirm updates the existing `Skill` row after verifying `skill.content_hash == session.base_content_hash`.
- Final confirm stores the changelog draft on the new `SkillRevision` row, not in `SKILL.md`.
- If the hash changed, return `409` with a conflict payload. The UI should offer "reload latest and reapply suggestion" instead of overwriting.

Create a second router for installed-skill evaluation history.

Create `backend/app/routers/skill_evaluations.py` with prefix `/api/skills/{skill_id}/evaluations`.

Endpoints:

```text
GET  /api/skills/{skill_id}/evaluations
POST /api/skills/{skill_id}/evaluations
GET  /api/skills/{skill_id}/evaluations/{evaluation_set_id}
PATCH /api/skills/{skill_id}/evaluations/{evaluation_set_id}
DELETE /api/skills/{skill_id}/evaluations/{evaluation_set_id}
GET  /api/skills/{skill_id}/evaluations/{evaluation_set_id}/runs
POST /api/skills/{skill_id}/evaluations/{evaluation_set_id}/estimate
POST /api/skills/{skill_id}/evaluations/{evaluation_set_id}/runs
GET  /api/skills/{skill_id}/evaluations/{evaluation_set_id}/runs/{run_id}
POST /api/skills/{skill_id}/evaluations/{evaluation_set_id}/runs/{run_id}/cancel
```

Installed-skill evaluation behavior:

- `GET /evaluations` returns evaluation sets with latest run summary attached.
- `POST /evaluations` creates a reusable evaluation set for the skill.
- `POST /estimate` returns expected case count, model calls, timeout, and approximate time/cost before a run starts.
- `POST /runs` reruns the selected evaluation set against the current skill bytes.
- `POST /runs/{run_id}/cancel` marks a queued/running evaluation as cancelled when the runner can stop safely.
- Each run snapshots `skill.content_hash`, `skill.version`, runner model, run config, and benchmark result.
- If a skill is edited after a run, old runs remain visible with a "previous skill version" indicator.
- Installed marketplace skills and user-created skills use the same endpoints because both are normal `Skill` rows after installation.

Create revision history endpoints in `backend/app/routers/skills.py` or a focused `backend/app/routers/skill_revisions.py`.

Endpoints:

```text
GET  /api/skills/{skill_id}/revisions
GET  /api/skills/{skill_id}/revisions/{revision_id}
POST /api/skills/{skill_id}/revisions/{revision_id}/rollback
```

Revision behavior:

- `GET /revisions` returns revision summaries ordered newest first.
- `GET /revisions/{revision_id}` returns changelog, changed files, compatibility result, evaluation summary, and metadata.
- `POST /rollback` requires CSRF and restores the selected revision by creating a new current rollback revision.
- Rollback returns the updated `SkillResponse` plus the new revision summary.
- Rollback marks latest evaluation summary stale by content-hash comparison; it does not mutate old eval rows.

Add installed package export support to the existing Skill API.

Endpoint:

```text
GET /api/skills/{skill_id}/export?include_evals=false
```

Export behavior:

- Only package skills can be exported; text skills return `INVALID_SKILL_PACKAGE`.
- The response is an `application/zip` attachment named `{skill.slug}.skill`.
- The exported archive keeps the package folder wrapper and includes the current installed files.
- `evals/` is excluded by default so portable `.skill` downloads stay focused on runtime instructions and assets.
- `include_evals=true` is an explicit escape hatch for internal backup/debug workflows.

Required behavior:

- All mutating endpoints use `Depends(verify_csrf)`.
- All session reads enforce `session.user_id == user.id`.
- All installed-skill evaluation endpoints enforce `skill.user_id == user.id`.
- Unknown or unauthorized sessions return the same `404` shape as existing resources.
- Evaluation run creation checks required credential bindings before creating a run row. Missing credentials returns `MARKETPLACE_CREDENTIAL_REQUIRED` and records `skill_evaluation.credential_missing`.
- Router/service code records the audit actions from the Credential-Aware Sandbox And Audit Policy section, with sanitized metadata only.
- `confirm` is idempotent:
  - `COMPLETED + finalized_skill_id` returns the existing `SkillResponse`.
  - `CONFIRMING` returns `409`.
  - `REVIEW` can transition to `CONFIRMING`.
  - Any status other than `REVIEW` or already `COMPLETED` returns `409`.

Router registration:

- Add `skill_builder` to the local router import block inside the `backend/app/main.py` app factory.
- Add `skill_evaluations` to the same local router import block.
- Add `skill_revisions` to the same local router import block.
- Add `app.include_router(skill_builder.router)` near the existing `app.include_router(skills.router)` registration in `backend/app/main.py`.
- Add `app.include_router(skill_evaluations.router)` near the existing `app.include_router(skills.router)` registration in `backend/app/main.py`.
- Add `app.include_router(skill_revisions.router)` near the existing `app.include_router(skills.router)` registration in `backend/app/main.py`.

## Backend File Plan

Create:

- `backend/app/models/skill_builder_session.py`
- `backend/app/models/skill_evaluation.py`
- `backend/app/models/skill_revision.py`
- `backend/app/schemas/skill_builder.py`
- `backend/app/schemas/skill_evaluation.py`
- `backend/app/schemas/skill_revision.py`
- `backend/app/services/skill_builder_service.py`
- `backend/app/services/skill_evaluation_service.py`
- `backend/app/services/skill_evaluation_worker.py`
- `backend/app/services/skill_health_service.py`
- `backend/app/services/skill_revision_service.py`
- `backend/app/services/skill_revision_storage.py`
- `backend/app/routers/skill_builder.py`
- `backend/app/routers/skill_evaluations.py`
- `backend/app/routers/skill_revisions.py`
- `backend/app/agent_runtime/skill_builder/__init__.py`
- `backend/app/agent_runtime/skill_builder/state.py`
- `backend/app/agent_runtime/skill_builder/agent.py`
- `backend/app/agent_runtime/skill_builder/graph.py`
- `backend/app/agent_runtime/skill_builder/deep_agent_worker.py`
- `backend/app/agent_runtime/skill_builder/prompt.md`
- `backend/app/skills/package_builder.py`
- `backend/app/skills/validator.py`
- `backend/app/skills/compatibility.py`
- `backend/app/agent_runtime/skill_builder/eval_runner.py`
- `backend/app/agent_runtime/skill_builder/deterministic_eval_execution.py`
- `backend/app/agent_runtime/skill_builder/eval_templates.py`
- `backend/app/agent_runtime/skill_builder/trigger_eval.py`
- `backend/alembic/versions/m64_skill_builder_sessions.py`
- `backend/app/skills/package_hash.py`
- `backend/scripts/backfill_skill_revisions.py`
- `backend/tests/test_skill_builder_service.py`
- `backend/tests/test_skill_builder_validator.py`
- `backend/tests/test_skill_builder_api.py`
- `backend/tests/test_skill_builder_package_builder.py`
- `backend/tests/test_skill_builder_eval_runner.py`
- `backend/tests/test_skill_builder_eval_templates.py`
- `backend/tests/test_skill_builder_trigger_eval.py`
- `backend/tests/test_skill_package_hash.py`
- `backend/tests/test_skill_evaluations_api.py`
- `backend/tests/test_skill_evaluation_service.py`
- `backend/tests/test_skill_evaluation_worker.py`
- `backend/tests/test_skill_health_service.py`
- `backend/tests/test_skill_revision_service.py`
- `backend/tests/test_skill_revision_backfill.py`
- `backend/tests/test_skill_revisions_api.py`
- `backend/tests/test_skill_compatibility.py`
- `backend/tests/test_skill_builder_audit.py`
- `backend/tests/test_skill_evaluation_audit.py`
- `backend/tests/test_skill_executor_credential_audit.py`

Modify:

- `backend/app/models/__init__.py`
- `backend/app/config.py`
- `backend/app/main.py`
- `backend/app/schemas/skill.py`
- `backend/app/skills/service.py`
- `backend/app/models/skill.py`
- `backend/app/marketplace/skill_runtime.py`
- `backend/app/agent_runtime/skill_executor.py`
- `backend/app/routers/skills.py` to create revisions for existing skill create/upload/update/file mutation paths and to include revision router behavior if not split into `skill_revisions.py`.

Do not add Skill Builder v1 files under `conversation_agent_protocol*` or route builder sessions through normal conversation run tables. The merged LangGraph v3 chat runtime is the normal Agent chat path; Skill Builder uses product-owned `skill_builder_sessions` and its own stream contract until a dedicated protocol migration is planned.

## Frontend File Plan

Create:

- `frontend/src/lib/types/skill-builder.ts`
- `frontend/src/lib/types/skill-evaluation.ts`
- `frontend/src/lib/types/skill-revision.ts`
- `frontend/src/lib/api/skill-builder.ts`
- `frontend/src/lib/api/skill-evaluations.ts`
- `frontend/src/lib/api/skill-revisions.ts`
- `frontend/src/lib/hooks/use-skill-builder.ts`
- `frontend/src/lib/hooks/use-skill-evaluations.ts`
- `frontend/src/lib/hooks/use-skill-revisions.ts`
- `frontend/src/lib/sse/stream-skill-builder-message.ts`
- `frontend/src/lib/sse/stream-skill-builder-resume.ts`
- `frontend/src/components/skill/skill-builder-dialog.tsx`
- `frontend/src/components/skill/skill-builder-chat.tsx`
- `frontend/src/components/skill/skill-builder-activity-strip.tsx`
- `frontend/src/components/skill/skill-builder-preview.tsx`
- `frontend/src/components/skill/skill-builder-validation.tsx`
- `frontend/src/components/skill/skill-builder-eval-panel.tsx`
- `frontend/src/components/skill/skill-evaluation-tab.tsx`
- `frontend/src/components/skill/skill-evaluation-run-detail.tsx`
- `frontend/src/components/skill/skill-history-tab.tsx`
- `frontend/src/components/skill/skill-revision-detail.tsx`
- `frontend/src/components/skill/skill-detail-tabs.tsx`
- `frontend/src/components/skill/skill-summary-strip.tsx`
- `frontend/src/components/skill/skill-evaluation-summary-badge.tsx`
- `frontend/src/components/skill/skill-health-badge.tsx`
- `frontend/src/components/skill/portable-compatibility-panel.tsx`
- `frontend/src/components/skill/__tests__/skill-builder-preview.test.tsx`
- `frontend/src/components/skill/__tests__/skill-builder-dialog.test.tsx`
- `frontend/src/components/skill/__tests__/skill-detail-tabs.test.tsx`
- `frontend/src/components/skill/__tests__/skill-detail-dialog.test.tsx`
- `frontend/src/components/skill/__tests__/skill-evaluation-tab.test.tsx`
- `frontend/src/components/skill/__tests__/skill-evaluation-summary-badge.test.tsx`
- `frontend/src/components/skill/__tests__/skill-history-tab.test.tsx`

Modify:

- `frontend/src/app/skills/page.tsx`
- `frontend/src/components/skill/skill-create-dialog.tsx`
- `frontend/src/components/skill/skill-detail-dialog.tsx`
- `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog.tsx`
- `frontend/src/lib/types/skill.ts`
- `frontend/messages/ko.json`
- `frontend/messages/en.json`

The `stream-skill-builder-*` files are builder authoring stream helpers. They may share low-level parsing/resume utilities with existing SSE helpers, but must not import normal chat `useChatRuntime`, `ChatRuntimeSection`, or `useMoldyLangGraphStream` in v1.

## Skill Menu Screen Plan

This section plans the `/skills` menu and related skill selection surfaces from the actual current source.

### Navigation Decision

Keep the app-level navigation as one `Skills` destination.

Rationale:

- Credentials, evaluation, history, compatibility, rollback, and "Improve by chat" are management surfaces of a skill object, not independent first-level destinations.
- Separate top-level menu items would split the same object across multiple places and make users choose between "Skills", "Skill Evaluations", or "Skill History" for the same task.
- A single `/skills` entry keeps creation, quality checks, credential binding, revision history, and marketplace actions anchored to the skill the user is already inspecting.

Navigation structure:

- Sidebar or primary navigation: keep only `Skills`.
- `/skills` page: list, search, kind tabs, state filters, and the primary `대화로 만들기` action.
- Skill detail dialog: `Content` or `Files`, `Credentials`, `Evaluation`, `History`, and `Metadata` tabs, shown conditionally.
- Deep links may open a specific detail tab, for example `/skills?detailId=<id>&tab=evaluation`, but they still land inside `/skills`.
- Admin-wide queues can become separate settings/admin surfaces later only if they cut across many skills, for example a global failed-evaluation queue or marketplace moderation queue.

### `/skills` Resource Page

Current source:

- Page shell: `frontend/src/app/skills/page.tsx:101`
- Primary action currently opens `openCreate('text')` at `frontend/src/app/skills/page.tsx:106`
- Empty state currently opens `openCreate('text')` at `frontend/src/app/skills/page.tsx:121`
- Cards are rendered by `SkillCard` at `frontend/src/app/skills/page.tsx:209`

Planned changes:

- Change `CreateTab` from `'text' | 'package' | 'scratch'` to `'chat' | 'text' | 'package'`.
- Make the page primary CTA open the chat builder by default:

```tsx
<Button onClick={() => openCreate("chat")}>
  <Sparkles className="size-4" />
  {t("buildWithChat")}
</Button>
```

- Keep text creation and package upload inside the create dialog as secondary tabs, not as separate page-level buttons.
- Change the empty state action to `openCreate('chat')` so first-time users start with the guided builder.
- Keep existing kind tabs (`all`, `text`, `package`) and search behavior unchanged.
- Add state filter chips inside `/skills`, not new navigation entries:
  - all
  - needs credentials
  - needs rerun
  - evaluation failed
  - published
  - local/draft
- Add evaluation summary to each `SkillCard` using `skill.latest_evaluation_summary`:
  - completed: show pass rate, for example `평가 86%`
  - stale: show `재평가 필요`
  - no completed run: show `평가 없음`
  - failed latest run: show `평가 실패`
- Use `ResourceListCard.StatusRow` for the evaluation badge when marketplace badges are present. If there are no marketplace badges, still render a status row when an evaluation summary exists.
- Keep card footer actions as `Publish` and `Manage`; do not add a card-level rerun action because reruns need evaluation set selection and evidence review.

Card data mapping:

```tsx
const latest = skill.latest_evaluation_summary;
const isStale = Boolean(
  latest?.skill_content_hash &&
  latest.skill_content_hash !== skill.content_hash,
);
```

### Create Skill Dialog

Current source:

- `SkillCreateDialog` uses `DialogShell size="lg" height="fixed"` in `frontend/src/components/skill/skill-create-dialog.tsx:37`.
- Current tabs are rendered at `frontend/src/components/skill/skill-create-dialog.tsx:70`.
- The current `ScratchTab` builds a JSZip package in browser at `frontend/src/components/skill/skill-create-dialog.tsx:255`.

Planned changes:

- Replace the `scratch` tab with `chat`.
- Rename visible tab copy:
  - Korean: `대화로 만들기`
  - English: `Build by chat`
- Remove browser-side JSZip creation from `ScratchTab`. Package generation moves to backend builder sessions.
- Use `SkillBuilderDialog` as the single chat-builder implementation. Do not create a separate `SkillBuilderTab` abstraction in v1; `SkillCreateDialog` should delegate to `SkillBuilderDialog` when `initialTab === "chat"` so the chat surface can use the larger shell consistently.
- For chat mode, use a larger shell:

```tsx
<DialogShell
  open={open}
  onOpenChange={onOpenChange}
  size={initialTab === 'chat' ? 'xl' : 'lg'}
  height={initialTab === 'chat' ? 'tall' : 'fixed'}
>
```

- Keep the existing `TextTab` and `PackageTab` intact except for shared `TabKey` changes.
- Change the creation callback so the builder can open the finalized skill directly on the evaluation tab when evals were generated:

```ts
type SkillCreatedOptions = {
  openTab?: 'content' | 'credentials' | 'evaluation' | 'history' | 'metadata'
}

onCreated?: (skillId: string, options?: SkillCreatedOptions) => void
```

Create dialog completion behavior:

- Text tab: `onCreated(created.id, { openTab: 'content' })`
- Package upload tab: `onCreated(created.id, { openTab: 'content' })`
- Chat builder with evals: `onCreated(created.id, { openTab: 'evaluation' })`
- Chat builder without evals: `onCreated(created.id, { openTab: 'content' })`

### Conversational Builder Screen

Create these components:

- `frontend/src/components/skill/skill-builder-dialog.tsx`
- `frontend/src/components/skill/skill-builder-chat.tsx`
- `frontend/src/components/skill/skill-builder-preview.tsx`
- `frontend/src/components/skill/skill-builder-validation.tsx`
- `frontend/src/components/skill/skill-builder-eval-panel.tsx`

Recommended layout:

```text
SkillBuilderDialog
┌────────────────────────────────────────────────────────────────┐
│ Header: 새 스킬 · 대화로 만들기                                  │
├───────────────────────────────┬────────────────────────────────┤
│ Chat                           │ Preview                        │
│ - user / assistant messages    │ - Files                        │
│ - clarification questions      │ - SKILL.md                     │
│ - composer                     │ - Validation                   │
│ - streaming status             │ - Evaluation                   │
├───────────────────────────────┴────────────────────────────────┤
│ Footer: 취소 · 검증 · 평가 실행 · 스킬 만들기                    │
└────────────────────────────────────────────────────────────────┘
```

Builder interaction states:

- `idle`: show initial prompt composer.
- `streaming`: disable destructive footer actions and show stream status.
- `draft_ready`: enable validation and preview.
- `validation_failed`: keep create disabled and highlight error-level issues.
- `validation_warning`: allow create, show warning count.
- `evaluating`: disable create only if the current draft is being mutated; otherwise allow user to keep chatting.
- `ready_to_confirm`: enable final create action.
- `completed`: close builder and open `SkillDetailDialog` for the new skill.

Preview panel tabs:

- `Files`: tree of `draft_package.files` with text preview.
- `SKILL.md`: direct markdown preview of the main instruction file.
- `Validation`: structured issues from `validate_draft_package`.
- `Compatibility`: portable compatibility report for OpenAI/Codex, Claude Code, and advisory Vercel Agent Skills checks.
- `Changelog`: generated human-readable summary shown before create/apply.
- `Evaluation`: latest builder eval result, with a button to run or rerun evals.
- `Metadata`: generated `agents/openai.yaml`, `agents/moldy.yaml`, credential requirements, and execution profile.

Default density rules:

- Start with `Files`, `SKILL.md`, and `Validation` visible.
- Reveal `Compatibility`, `Changelog`, `Evaluation`, and `Metadata` only when the section has content, a warning/error, an active run, or an explicit user action.
- Do not expose raw JSON by default. Use collapsible code views for advanced metadata.

### Skill Detail Dialog

Current source:

- `SkillDetailBody` branches by `skill.kind` at `frontend/src/components/skill/skill-detail-dialog.tsx:108`.
- `TextSkillEditor` renders credential bindings and textarea in one body at `frontend/src/components/skill/skill-detail-dialog.tsx:180`.
- `PackageSkillEditor` renders file tree, credential bindings, editor, and footer in `frontend/src/components/skill/skill-detail-dialog.tsx:466`.
- `DialogShell.Body` is a single flex body in `frontend/src/components/shared/dialog-shell.tsx:100`, so the detail dialog should not stack multiple body regions.

Existing skill improvement entry point:

- Add a header or footer action named `대화로 개선` / `Improve by chat`.
- The action opens the same `SkillBuilderDialog` in `mode="improve"` with `sourceSkillId={skill.id}`.
- The first builder message should be prefilled from the user action when supplied, or use a default prompt:

```text
이 스킬을 더 안정적이고 평가 가능한 형태로 개선해줘.
```

- The builder preview should show:
  - original files
  - proposed files
  - added/changed/deleted file summary
  - generated changelog summary
  - portable compatibility result
  - validation issues
  - evaluation result before apply, when available
- The apply button copy should be `변경 적용` / `Apply changes`, not `스킬 만들기`.
- If a hash conflict occurs, show:
  - current skill changed since this improvement session started
  - reload latest and reapply suggestion
  - discard session
- Do not auto-apply generated changes while chatting.

Planned structure:

- Add `SkillDetailTab = 'content' | 'credentials' | 'evaluation' | 'history' | 'metadata'`.
- Add `initialTab?: SkillDetailTab` to `SkillDetailDialog`.
- Add `SkillDetailTabs` to render the tab list inside a single `DialogShell.Body`.
- Split existing editor internals so the dialog has one body and one footer:
  - `TextSkillContentTab`
  - `PackageSkillFilesTab`
  - `SkillCredentialsTab`
  - `SkillEvaluationTab`
  - `SkillHistoryTab`
  - `SkillMetadataTab`
  - `SkillDetailFooter`
- Keep package file editing behavior intact. The package `content` tab should still use the current `DialogShell.Split` mental model: file tree on the left, editor on the right.
- Move `SkillCredentialBindingsPanel` out of the top of content editors and into the `Credentials` tab. This removes repeated credential UI from the editing surface while preserving all existing credential APIs.

Tab order:

```text
Text skill:    Content · Credentials · Evaluation · History · Metadata
Package skill: Files   · Credentials · Evaluation · History · Metadata
```

Footer behavior:

- `Content` / `Files`: show delete, close, and save actions exactly as today.
- `Credentials`: show close only; credential changes are saved immediately by the existing binding mutations.
- `Evaluation`: show close, `평가 다시 실행`, and `평가 취소` while a run is queued/running/grading.
- `History`: show close and `이 버전으로 되돌리기` when a non-current revision is selected.
- `Metadata`: show close and save metadata only if editable fields changed.

Metadata tab:

- Use existing `useUpdateSkillMetadata` from `frontend/src/lib/hooks/use-skills.ts:62`.
- Show editable `name`, `description`, and `version`.
- Show read-only `slug`, `kind`, `content_hash`, `size_bytes`, `used_by_count`, `origin_summary`, and `publication_summary`.
- This tab is not required for evaluation, but it gives the detail dialog a clean place for properties that currently live only in the header/card.

History tab:

- Create `SkillHistoryTab` with a left list of revisions and a right detail pane.
- Show revision number, operation, created date, changelog summary, current marker, content hash, and file count.
- Detail pane shows changelog items, changed files, compatibility result, and evaluation summary snapshot.
- Selecting the current revision disables rollback.
- Selecting an older revision enables `이 버전으로 되돌리기`.
- Rollback action calls `POST /api/skills/{skill_id}/revisions/{revision_id}/rollback`, shows a confirm dialog, then refreshes skill detail, skill list, evaluation summary, and revisions.
- After rollback, open the new rollback revision detail and show a success toast.

### Installed Skill Evaluation Tab

Create:

- `frontend/src/components/skill/skill-evaluation-tab.tsx`
- `frontend/src/components/skill/skill-evaluation-run-detail.tsx`
- `frontend/src/components/skill/skill-evaluation-summary-badge.tsx`

Evaluation tab layout:

```text
Evaluation tab
┌───────────────────────────────────────────────────────────────┐
│ Summary strip: latest pass rate · trigger accuracy · duration │
│ Actions: 평가 다시 실행 · 평가 세트 만들기                      │
├──────────────────────────┬────────────────────────────────────┤
│ Evaluation sets / runs    │ Selected run detail                 │
│ - Builder generated set   │ - with-skill vs baseline metrics    │
│ - Manual set              │ - per-case pass/fail table          │
│ - Run history             │ - evidence and grader feedback      │
└──────────────────────────┴────────────────────────────────────┘
```

States:

- No evaluation sets: show an empty state with `평가 세트 만들기`.
- Evaluation set exists but no runs: show set details and primary `평가 실행`.
- Estimate ready: show case count, model call count, estimated time, timeout, approximate cost, and baseline comparison before creating the run.
- Running: show queued/running/grading status and keep previous completed run visible.
- Completed: show latest summary and selected run detail.
- Failed: show error message, failed stage, and allow rerun.
- Cancelled: show cancellation time and allow rerun.
- Stale: show `이 평가는 이전 스킬 버전 기준입니다` when `run.skill_content_hash !== skill.content_hash`.

Data flow:

- `useSkillEvaluationSets(skillId)` loads sets with latest run summaries.
- Selecting a set loads `useSkillEvaluationRuns(skillId, evaluationSetId)`.
- Selecting a run loads `useSkillEvaluationRun(skillId, evaluationSetId, runId)`.
- `useEstimateSkillEvaluation` loads the confirmation payload before rerun.
- `useRunSkillEvaluation` creates a new run and invalidates:
  - `['skills']`
  - `['skills', skillId]`
  - `['skills', skillId, 'evaluations']`
  - `['skills', skillId, 'evaluations', evaluationSetId, 'runs']`
- `useCancelSkillEvaluation` cancels an active run and invalidates the same keys.

### Skill Cards And Agent Skill Picker

`SkillEvaluationSummaryBadge` and `SkillHealthBadge` should be reusable in:

- `/skills` cards in `frontend/src/app/skills/page.tsx:209`
- agent settings `ToolsSkillsDialog` skill rows in `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog.tsx:511`

Badge variants:

```ts
type SkillEvaluationBadgeState =
  | "passed"
  | "partial"
  | "failed"
  | "stale"
  | "missing"
  | "running"
  | "cancelled";
```

Mapping:

- `missing`: no `latest_evaluation_summary`
- `running`: latest run status is `queued`, `running`, or `grading`
- `cancelled`: latest run status is `cancelled`
- `failed`: latest run status is `failed` or pass rate below 0.5
- `partial`: pass rate is at least 0.5 and below 0.8
- `passed`: pass rate is at least 0.8 and not stale
- `stale`: latest summary content hash differs from current `skill.content_hash`

In `ToolsSkillsDialog`, keep the row compact:

- Selected column subtitle: `Skill · 평가 86%` or `Skill · 평가 없음`
- Available skill row subtitle: `Package · 평가 86%` or `Text · 재평가 필요`
- Do not add run detail or rerun controls to this dialog.

Use `skill.health` when present for top-level labels:

- `ready`: `검증됨`
- `needs_evaluation`: `평가 없음`
- `needs_rerun`: `재평가 필요`
- `needs_credentials`: `자격증명 필요`
- `evaluation_running`: `평가 중`
- `evaluation_failed`: `평가 실패`
- `low_confidence`: `낮은 통과율`

### Copy And I18n

Add or replace copy under `skill` in both locale files.

New Korean keys:

```json
{
  "buildWithChat": "대화로 만들기",
  "improveWithChat": "대화로 개선",
  "applyImprovement": "변경 적용",
  "improvementConflict": "이 개선 세션을 시작한 뒤 스킬이 변경되었습니다.",
  "changelog": {
    "title": "변경 요약",
    "empty": "표시할 변경 요약이 없습니다."
  },
  "compatibility": {
    "title": "공용 호환성",
    "openaiCodex": "OpenAI/Codex",
    "claudeCode": "Claude Code",
    "vercelAgentSkills": "Vercel Agent Skills"
  },
  "history": {
    "tab": "이력",
    "current": "현재 버전",
    "rollback": "이전 버전으로 되돌리기",
    "rollbackConfirm": "이전 버전으로 되돌리면 현재 내용은 새 이력으로 보존됩니다.",
    "rollbackDone": "이전 버전으로 되돌렸습니다."
  },
  "evaluation": {
    "tab": "평가",
    "none": "평가 없음",
    "passed": "평가 {rate}%",
    "partial": "일부 통과 {rate}%",
    "failed": "평가 실패",
    "running": "평가 중",
    "cancelled": "평가 취소됨",
    "stale": "재평가 필요",
    "rerun": "평가 다시 실행",
    "cancel": "평가 취소",
    "createSet": "평가 세트 만들기",
    "estimate": "예상 {caseCount}개 케이스 · 약 {seconds}초",
    "previousVersion": "이 평가는 이전 스킬 버전 기준입니다"
  },
  "health": {
    "ready": "검증됨",
    "needsEvaluation": "평가 없음",
    "needsRerun": "재평가 필요",
    "needsCredentials": "자격증명 필요",
    "evaluationRunning": "평가 중",
    "evaluationFailed": "평가 실패",
    "lowConfidence": "낮은 통과율"
  }
}
```

New English keys:

```json
{
  "buildWithChat": "Build by chat",
  "improveWithChat": "Improve by chat",
  "applyImprovement": "Apply changes",
  "improvementConflict": "This skill changed after the improvement session started.",
  "changelog": {
    "title": "Change summary",
    "empty": "No change summary to show."
  },
  "compatibility": {
    "title": "Portable compatibility",
    "openaiCodex": "OpenAI/Codex",
    "claudeCode": "Claude Code",
    "vercelAgentSkills": "Vercel Agent Skills"
  },
  "history": {
    "tab": "History",
    "current": "Current version",
    "rollback": "Roll back to this version",
    "rollbackConfirm": "Rolling back preserves the current content as a new history entry.",
    "rollbackDone": "Rolled back to the selected version."
  },
  "evaluation": {
    "tab": "Evaluation",
    "none": "No evaluation",
    "passed": "Evaluation {rate}%",
    "partial": "Partial {rate}%",
    "failed": "Evaluation failed",
    "running": "Evaluating",
    "cancelled": "Cancelled",
    "stale": "Needs rerun",
    "rerun": "Run evaluation again",
    "cancel": "Cancel evaluation",
    "createSet": "Create evaluation set",
    "estimate": "{caseCount} cases · about {seconds}s",
    "previousVersion": "This run used an older skill version"
  },
  "health": {
    "ready": "Verified",
    "needsEvaluation": "No evaluation",
    "needsRerun": "Needs rerun",
    "needsCredentials": "Needs credentials",
    "evaluationRunning": "Evaluating",
    "evaluationFailed": "Evaluation failed",
    "lowConfidence": "Low pass rate"
  }
}
```

While editing `frontend/messages/en.json`, also replace generic current strings in the skill menu scope such as `Create Dialog`, `Description Placeholder`, `Submit`, and `Filtered` with product-specific English copy. This avoids adding polished new UI beside placeholder old UI.

## Skill Builder Service Contract

Create `backend/app/services/skill_builder_service.py`.

Public functions:

```python
async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    user_request: str,
    *,
    mode: SkillBuilderMode = SkillBuilderMode.CREATE,
    source_skill_id: uuid.UUID | None = None,
) -> SkillBuilderSession
async def get_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> SkillBuilderSession | None
async def append_message(db: AsyncSession, session: SkillBuilderSession, *, role: str, content: str) -> None
async def load_skill_snapshot(db: AsyncSession, skill: Skill) -> dict[str, Any]
async def save_draft_package(db: AsyncSession, session: SkillBuilderSession, draft: SkillDraftPackage) -> SkillBuilderSession
async def save_validation_result(db: AsyncSession, session: SkillBuilderSession, result: dict[str, Any]) -> SkillBuilderSession
async def save_compatibility_result(db: AsyncSession, session: SkillBuilderSession, result: dict[str, Any]) -> SkillBuilderSession
async def save_changelog_draft(db: AsyncSession, session: SkillBuilderSession, changelog: dict[str, Any]) -> SkillBuilderSession
async def claim_for_confirming(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> bool
async def confirm_session(db: AsyncSession, session: SkillBuilderSession, user: CurrentUser) -> Skill
```

Create-mode confirm flow:

1. Load `SkillDraftPackage` from `session.draft_package`.
2. Validate draft with `app.skills.validator.validate_draft_package`.
3. Reject if any issue has `severity == "error"`.
4. Build zip bytes with `app.skills.package_builder.build_skill_zip_bytes`.
5. Call `skill_service.create_package_skill(db, user_id=user.id, zip_bytes=zip_bytes)`.
6. Resolve package storage path.
7. Run `scan_package(resolve_data_path(skill.storage_path))`.
8. If findings exist, delete the skill row and directory, rollback, and raise a 400 error.
9. Set:
   - `skill.credential_requirements = draft.credential_requirements or None`
   - `skill.execution_profile = draft.execution_profile or None`
   - `skill.origin_kind = "created_by_me"`
   - `skill.source_kind = "user"`
10. If the draft contains `evals/evals.json` or `session.eval_result`, create a `SkillEvaluationSet` linked to the new `skill.id`.
11. If builder-time eval runs already completed, create a `SkillEvaluationRun` linked to that set and copy the benchmark/case results into it.
12. Create a `SkillRevision` with `operation="builder_create"`, `compatibility_result=session.compatibility_result`, and `changelog_summary=session.changelog_draft.summary` when present.
13. Set `skill.current_revision_id` to that revision.
14. Set `session.status = COMPLETED`, `session.finalized_skill_id = skill.id`.
15. Commit and return the serialized skill through the router.

This deliberately reuses `create_package_skill` so path safety and package metadata stay centralized.

Improve-mode start flow:

1. Require `source_skill_id`.
2. Load the skill through `skill_service.get_skill(db, source_skill_id, user.id)`.
3. Capture `base_skill_version`, `base_content_hash`, and `base_snapshot`.
4. For text skills, create a draft package with one `SKILL.md` file from existing content and metadata.
5. For package skills, load current package files into `draft_package.files`.
6. Set `session.mode = "improve"` and `session.source_skill_id = source_skill_id`.

Improve-mode confirm flow:

1. Load `source_skill_id` through `skill_service.get_skill`.
2. If `skill.content_hash != session.base_content_hash`, return a `409` conflict and do not write.
3. Validate the draft package.
4. Run secret scan against a temporary materialized package.
5. For text skills:
   - require exactly one `SKILL.md`-equivalent draft file
   - update content through existing text content service behavior
   - update metadata if name, description, or version changed
6. For package skills:
   - apply changed files through existing `set_skill_file`
   - delete files removed from the draft except protected `SKILL.md`
   - create new files through existing package file APIs
7. Update `skill.credential_requirements` and `skill.execution_profile` from `agents/moldy.yaml` or draft metadata.
8. Attach or update evaluation sets generated during improvement.
9. Mark previous evaluation summaries stale by hash comparison; do not mutate historical runs just to mark them stale.
10. Create a `SkillRevision` with `operation="builder_improvement"`, changed file metadata, compatibility result, and changelog draft.
11. Set `skill.current_revision_id` to that revision.
12. Set `session.status = COMPLETED`, `session.finalized_skill_id = skill.id`.
13. Commit and return the updated `SkillResponse`.

## Skill Revision Service

Create `backend/app/services/skill_revision_service.py` and `backend/app/services/skill_revision_storage.py`.

Public functions:

```python
async def create_revision_for_skill(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    operation: str,
    source_session_id: uuid.UUID | None = None,
    restored_from_revision_id: uuid.UUID | None = None,
    changed_files: list[dict[str, Any]] | None = None,
    changelog: dict[str, Any] | None = None,
    compatibility_result: dict[str, Any] | None = None,
    evaluation_summary: dict[str, Any] | None = None,
) -> SkillRevision
async def list_revisions(db: AsyncSession, *, skill: Skill, user_id: uuid.UUID) -> list[SkillRevision]
async def get_revision(db: AsyncSession, *, skill: Skill, user_id: uuid.UUID, revision_id: uuid.UUID) -> SkillRevision | None
async def rollback_to_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    revision: SkillRevision,
) -> tuple[Skill, SkillRevision]
```

Implementation rules:

- Reuse `skill_service.get_skill(db, skill_id, user_id)` before calling revision functions from routers.
- Use the same zip path safety rules as `package_builder`.
- Do not persist full file contents in `SkillRevision.changed_files`; use paths, operations, byte counts, and hashes.
- Use `operation` values from the Skill Revision History section.
- On rollback, restore bytes from the revision zip and then create a new revision with `operation="rollback"`.
- On rollback, write `skill_revision.rollback` audit metadata with only IDs, revision numbers, and hashes.

## Installed Skill Evaluation Service

Create `backend/app/services/skill_evaluation_service.py`.

Public functions:

```python
async def list_evaluation_sets(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillEvaluationSet]

async def create_evaluation_set(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    name: str,
    description: str | None,
    evals: list[dict[str, Any]],
    source_kind: str = "manual",
    template_key: str | None = None,
    template_version: str | None = None,
    generation_strategy: dict[str, Any] | None = None,
) -> SkillEvaluationSet

async def list_runs(
    db: AsyncSession,
    *,
    evaluation_set: SkillEvaluationSet,
    user_id: uuid.UUID,
) -> list[SkillEvaluationRun]

async def create_run(
    db: AsyncSession,
    *,
    skill: Skill,
    evaluation_set: SkillEvaluationSet,
    user_id: uuid.UUID,
    run_config: dict[str, Any] | None = None,
    estimate: dict[str, Any] | None = None,
) -> SkillEvaluationRun

async def estimate_run(
    *,
    evaluation_set: SkillEvaluationSet,
    run_config: dict[str, Any] | None = None,
) -> dict[str, Any]

async def mark_run_completed(
    db: AsyncSession,
    *,
    run: SkillEvaluationRun,
    summary: dict[str, Any],
    benchmark: dict[str, Any],
    case_results: list[dict[str, Any]],
    artifact_path: str | None,
) -> SkillEvaluationRun

async def cancel_run(
    db: AsyncSession,
    *,
    run: SkillEvaluationRun,
    user_id: uuid.UUID,
) -> SkillEvaluationRun
```

Ownership and version rules:

- Always load the parent `Skill` through `skill_service.get_skill(db, skill_id, user.id)` before any evaluation operation.
- `SkillEvaluationSet.skill_id` and `SkillEvaluationRun.skill_id` must match the path `skill_id`.
- `create_run` snapshots `skill.content_hash` and `skill.version` before execution.
- `create_run` stores `runner_version`, `grader_prompt_version`, and `eval_schema_version`.
- `estimate_run` must not create DB rows. It returns the confirmation payload for the UI.
- `cancel_run` only transitions `queued`, `running`, or `grading` runs to `cancelled`.
- The UI should mark a run stale relative to the current skill when `run.skill_content_hash != skill.content_hash`.

Latest evaluation summary:

- Add an optional `latest_evaluation_summary` field to `SkillResponse` and `SkillBrief`.
- Populate it in list/detail serializers with one latest completed run per skill.
- Keep the field compact:

```json
{
  "run_id": "uuid",
  "evaluation_set_id": "uuid",
  "status": "completed",
  "pass_rate": 0.86,
  "trigger_accuracy": 0.9,
  "completed_at": "2026-06-13T00:00:00",
  "skill_content_hash": "6f9b6c2d1b4a3f5e8d7c9a0b2e1f4c6d8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d"
}
```

## Internal Evaluation Templates

Claude Code's skill creator does not ask the user to pick a visible preset. It generates test prompts from the intended skill behavior, runs comparisons, grades outputs, aggregates results, and then improves the skill or trigger description. Moldy should preserve that automatic loop.

Create `backend/app/agent_runtime/skill_builder/eval_templates.py`.

Use internal templates only. They are not a normal user-facing picker.

Template keys:

- `summarization`
- `structured_extraction`
- `research`
- `file_generation`
- `tool_or_api_usage`
- `workflow_or_planning`
- `generic_instruction`

Public types and functions:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


EvalTemplateKey = Literal[
    "summarization",
    "structured_extraction",
    "research",
    "file_generation",
    "tool_or_api_usage",
    "workflow_or_planning",
    "generic_instruction",
]


@dataclass(frozen=True)
class EvalTemplate:
    key: EvalTemplateKey
    version: str
    default_case_count: int
    expectation_hints: list[str]
    risk_checks: list[str]


def select_eval_template(intent: dict[str, Any], draft_package: dict[str, Any]) -> EvalTemplate:
    """Choose the best internal evaluation template from the inferred skill intent and files."""
```

Selection rules:

- If the draft promises tables, JSON, fields, action items, owners, dates, or extraction, choose `structured_extraction`.
- If the draft promises search, citations, sources, or current information, choose `research`.
- If the draft writes docs, code files, spreadsheets, images, or artifacts, choose `file_generation`.
- If `execution_profile.tool_dependencies` or generated scripts call external APIs, choose `tool_or_api_usage`.
- If the skill coordinates multiple steps without a strict output shape, choose `workflow_or_planning`.
- If no stronger signal exists, choose `generic_instruction`.

The builder should tell the user what it inferred in plain language only when useful:

```text
이 스킬은 회의록에서 구조화된 액션 아이템을 뽑는 유형으로 평가해볼게요.
```

Do not show a dropdown like "choose summarization/research/file_generation" in the default UI.

## Claude Code Eval Loop In Moldy

Keep the loop, but make it product-native:

1. Infer the eval template from the conversation and draft package.
2. Generate 2-3 eval cases by default.
3. Run the draft with the skill available.
4. Run a baseline without the skill when the case is comparable.
5. Grade outputs with a Moldy grader prompt using explicit expectations and evidence.
6. Aggregate pass rate, trigger accuracy, duration, token count, tool calls, and error count.
7. Suggest focused changes to `SKILL.md`, trigger description, references, or scripts.
8. Repeat only when the user clicks rerun or asks the builder to improve the skill.

Optional eval-case review:

- This is the "8번" from the brainstorming note.
- It is not a required step in the default flow.
- Show it only when:
  - the builder confidence is low
  - generated cases include user-sensitive examples
  - a user opens advanced evaluation editing
  - a user asks why a score changed
- The review UI lets users remove, edit, or add cases before a run.

## Skill Health Summary

Create `backend/app/services/skill_health_service.py`.

Skill Health answers "can I trust/use this skill right now?" without making users interpret raw benchmark details.

Health states:

```python
SkillHealthState = Literal[
    "ready",
    "needs_evaluation",
    "needs_rerun",
    "needs_credentials",
    "evaluation_running",
    "evaluation_failed",
    "low_confidence",
]
```

Inputs:

- latest evaluation summary
- current `skill.content_hash`
- credential requirements and current bindings
- latest validation result if available
- latest evaluation run status

Rules:

- `needs_credentials`: required credential requirements exist and are not bound.
- `evaluation_running`: latest run status is `queued`, `running`, or `grading`.
- `needs_evaluation`: no completed run exists.
- `needs_rerun`: latest run content hash differs from current skill content hash.
- `evaluation_failed`: latest run status is `failed` or `cancelled`.
- `low_confidence`: latest completed pass rate is below `0.8`.
- `ready`: required credentials are bound and latest completed pass rate is at least `0.8` for current content hash.

Add an optional `health` field to `SkillResponse` and `SkillBrief`:

```json
{
  "state": "needs_rerun",
  "label": "재평가 필요",
  "reason": "SKILL.md changed after the latest completed evaluation.",
  "severity": "warning"
}
```

Frontend should use `health` for the top-level card/status badge and keep `latest_evaluation_summary` for detailed metrics.

## Evaluation Cost And Time Guard

Before starting an installed-skill evaluation run, call the estimate endpoint.

Estimate response shape:

```json
{
  "case_count": 3,
  "model_call_count": 9,
  "estimated_seconds": 45,
  "timeout_seconds": 180,
  "estimated_cost_usd": 0.08,
  "uses_baseline_comparison": true
}
```

Default behavior:

- Builder-generated evals use 2-3 cases.
- Installed skill rerun uses the selected evaluation set but asks for confirmation when case count is greater than 5.
- Timeout status should be stored as `failed` with `error_message` beginning with `timeout:`.
- User cancellation should store status `cancelled`.
- The UI should show previous completed results while a new run is queued or running.

## Evaluation Execution Infrastructure

Create `backend/app/services/skill_evaluation_worker.py`.

Evaluation runs are durable background jobs:

- `POST /api/skills/{skill_id}/evaluations/{evaluation_set_id}/runs` creates a `SkillEvaluationRun` row with `status="queued"` and commits before execution starts.
- The router enqueues the run id into `SkillEvaluationWorker` and returns the run summary immediately.
- The worker uses its own DB session per phase and reloads the run, skill, and evaluation set by id.
- The worker updates status to `running`, then `grading`, then `completed`, `failed`, or `cancelled`.
- If the process restarts, queued or interrupted runs can be picked up by a startup reconciliation pass that moves stale `running`/`grading` rows to `failed` with `error_message="interrupted: process restarted"` for v1. A later distributed queue can resume jobs.

Do not store skill evaluation jobs in normal chat `conversation_runs`. Evaluation "run" is a skill-quality domain object persisted in `skill_evaluation_runs`. It can share user-facing status/cancel vocabulary with normal chat runs, but ownership, retention, evidence, cost estimate, and stale-by-content-hash behavior are separate.

Concurrency defaults:

- Add backend settings:
  - `SKILL_EVALUATION_ENABLED=true`
  - `SKILL_EVALUATION_MAX_CONCURRENT=1`
  - `SKILL_EVALUATION_QUEUE_MAX_SIZE=20`
  - `SKILL_EVALUATION_RUN_TIMEOUT_SECONDS=180`
  - `SKILL_EVALUATION_CASE_TIMEOUT_SECONDS=60`
- Use an `asyncio.Semaphore` in the worker. Default `1` is intentional even though the LangGraph checkpointer pool is now configurable with default max `10`; normal chat and builder sessions must remain responsive under E2E and local dev load.
- Document that `SKILL_EVALUATION_MAX_CONCURRENT` should stay below `CHECKPOINTER_POOL_MAX_SIZE` and reserve capacity for normal chat traffic.
- Reject run creation with `409` and code `SKILL_EVALUATION_QUEUE_FULL` when the in-process queue is full.
- Never hold a DB transaction open while waiting on model calls, subprocess execution, or file IO.

Model and cost owner:

- Use the same system model path as the hidden builder for grader/model-based evaluation in v1: `resolve_system_model(db, "text_primary")`.
- Store `runner_model`, `runner_version`, `grader_prompt_version`, `eval_schema_version`, and `estimate` on the run.
- Attribute cost as platform/system evaluation cost in run metadata. Do not silently consume a user's personal LLM credential for hidden evaluation unless a later settings screen explicitly supports that mode.
- If System LLM is missing, run creation returns `409 SYSTEM_LLM_NOT_CONFIGURED` before creating execution artifacts.

Cancellation:

- `cancel_run(...)` for `queued` runs moves directly to `cancelled`.
- `cancel_run(...)` for `running` or `grading` records status `cancelled` plus `cancellation_requested_at` and `cancellation_reason`; the worker's DB-backed cancellation probe treats that terminal row as the cooperative stop signal.
- The worker checks cancellation before each case, before each baseline run, before subprocess-timeout-sensitive work, before grading, and before final aggregation.
- Subprocess execution uses timeout and cooperative cancellation where available; if a subprocess cannot be interrupted cleanly, kill it at the case timeout and mark the run `cancelled` if cancellation was requested, otherwise `failed`.

Startup and shutdown:

- Register the worker in `backend/app/main.py` lifespan after DB setup.
- On startup, call `skill_evaluation_worker.reconcile_stale_runs(...)`.
- On shutdown, stop accepting new jobs and wait briefly for active jobs. If a job cannot finish, leave the durable row in `running`; the next startup reconciliation marks it interrupted.

## Draft Package Builder

Create `backend/app/skills/package_builder.py`.

Responsibilities:

- Normalize skill folder name to existing `skill_service.slugify` behavior.
- Ensure one `SKILL.md` exists.
- Write zip entries under a single top-level folder so existing `extract_package` handles both root and one-level layouts.
- Exclude builder-only eval artifacts from final export unless `include_evals=True`.
- Preserve POSIX paths.

Public functions:

```python
from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from pathlib import PurePosixPath

from app.schemas.skill_builder import SkillDraftFile
from app.skills.service import slugify


EXCLUDED_EXPORT_DIRS = {"evals"}


def normalize_draft_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/").lstrip("/")
    pure = PurePosixPath(cleaned)
    if not cleaned or ".." in pure.parts or "\x00" in cleaned:
        raise ValueError(f"invalid draft file path: {path!r}")
    return pure.as_posix()


def build_skill_zip_bytes(
    *,
    slug: str,
    files: Sequence[SkillDraftFile],
    include_evals: bool = False,
) -> bytes:
    folder = slugify(slug)
    by_path: dict[str, SkillDraftFile] = {}
    for file in files:
        rel = normalize_draft_path(file.path)
        if not include_evals and rel.split("/", 1)[0] in EXCLUDED_EXPORT_DIRS:
            continue
        by_path[rel] = file
    if "SKILL.md" not in by_path:
        raise ValueError("draft package must include SKILL.md")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(by_path):
            zf.writestr(f"{folder}/{rel}", by_path[rel].content)
    return buf.getvalue()
```

Tests:

- `test_build_skill_zip_requires_skill_md`
- `test_build_skill_zip_rejects_traversal`
- `test_build_skill_zip_excludes_evals_by_default`
- `test_build_skill_zip_can_be_imported_by_existing_packager`

## Skill Validator

Create `backend/app/skills/validator.py`.

Validation result shape:

```python
{
  "valid": true,
  "issues": [
    {
      "code": "description_too_short",
      "severity": "warning",
      "path": "SKILL.md",
      "message": "Description should include clear trigger contexts."
    }
  ],
  "summary": {
    "file_count": 4,
    "has_scripts": true,
    "has_references": true,
    "has_assets": false
  }
}
```

Rules:

- Error when `SKILL.md` is missing.
- Error when frontmatter `name` or `description` is missing or blank.
- Error when `name` is not lowercase kebab-case or exceeds 64 characters.
- Error when `description` exceeds 1024 characters.
- Warning when `description` is shorter than 80 characters.
- Warning when `description` does not include trigger words such as "Use when", "사용", "when", "whenever", or a concrete file/domain/task context.
- Warning when `SKILL.md` body exceeds 500 lines.
- Warning when `SKILL.md` contains scaffolding markers such as bracketed task markers, `Complete and informative`, `Replace with`, or HTML comments copied from the current scratch tab.
- Error when a referenced file path in Markdown points outside the draft package.
- Warning when `references/` exists but no reference file is mentioned from `SKILL.md`.
- Warning when `scripts/` exists but no script usage guidance appears in `SKILL.md`.
- Error when script files use unsupported executable extensions outside `.py`, `.js`, `.cjs`, `.mjs`, `.sh`.
- Error when secret scanner finds a high-signal secret after materializing draft files into a temporary directory and calling `scan_package()`.
- Warning when `agents/openai.yaml` exists but `interface.default_prompt` does not mention `$<skill-name>`.
- Warning when generated skills do not include `agents/openai.yaml`.
- Error when `agents/moldy.yaml` contains invalid `credential_requirements` entries:
  - each item must include `key`, `definition_key`, `required`, `label`
  - `injection` must be `env` or `config`
  - `scope` must be `user`, `system_dependency`, or `manual`
- Error when `SKILL.md` frontmatter contains Moldy-only keys listed in the Portable Compatibility Check section.
- Warning when `SKILL.md` contains generated changelog, rollback notes, eval result summaries, or compatibility reports.
- Warning when `SKILL.md` references absolute local paths, `localhost`, `.env`, `data/skills/`, or Moldy API routes.

Implementation note:

- Use `parse_skill_md` from `backend/app/skills/inspector.py`.
- Use `scan_package` from `backend/app/marketplace/secret_scan.py:431`.
- Use `check_portable_compatibility` from `backend/app/skills/compatibility.py`.
- For in-memory validation, materialize files under `/tmp` with `tempfile.TemporaryDirectory()` and then scan that directory.

## LangGraph Skill Builder

Create `backend/app/agent_runtime/skill_builder/state.py`.

State fields:

```python
from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import BaseMessage


class SkillBuilderState(TypedDict, total=False):
    messages: list[BaseMessage]
    user_id: str
    session_id: str
    user_request: str
    current_phase: int
    intent: dict[str, Any]
    draft_package: dict[str, Any]
    validation_result: dict[str, Any]
    compatibility_result: dict[str, Any]
    changelog_draft: dict[str, Any]
    eval_result: dict[str, Any]
    trigger_eval_result: dict[str, Any]
    next_action: str
```

Create `backend/app/agent_runtime/skill_builder/prompt.md`.

Prompt requirements:

- Explain that the builder creates portable agent skills, not Moldy-only snippets.
- Capture intent from the current conversation first.
- Ask at most two missing questions at a time.
- Default to instruction-only unless repeated deterministic work justifies scripts.
- Move detailed domain docs to `references/`.
- Use scripts only for deterministic, repeated, fragile, or file-transform work.
- Keep `SKILL.md` concise and under 500 lines.
- Do not put changelog, revision history, evaluation results, rollback notes, or Moldy-only settings into `SKILL.md`.
- Put trigger conditions in `description`, not only the body.
- Generate realistic test prompts when the user wants quality checking.
- Infer the internal evaluation template automatically from intent and draft files; do not ask users to choose a preset in the default flow.
- Offer eval-case review only as an advanced or low-confidence step.
- Generate a concise changelog draft when improving an existing skill:
  - `summary`: one sentence
  - `items`: 3-7 user-readable bullet items
  - `risk_notes`: compatibility or behavior risks
- Keep Moldy-specific runtime metadata in `agents/moldy.yaml` and session JSON.
- Never include real credentials, tokens, private keys, or copied `.env` contents.

Create `backend/app/agent_runtime/skill_builder/graph.py`.

Graph nodes:

- `load_context`: load session row and current draft.
- `classify_message`: decide whether the user is providing intent, feedback, approval, or an eval request.
- `collect_intent`: produce or update `intent`.
- `draft_package`: generate `SkillDraftPackage`.
- `validate_package`: call `validate_draft_package`.
- `check_compatibility`: call `check_portable_compatibility`.
- `generate_changelog`: compare base snapshot and draft package, then save `changelog_draft`.
- `review_response`: stream a concise human-facing summary and save status `REVIEW`.
- `apply_feedback`: update draft from user feedback.
- `run_evals`: Phase 2 eval execution.
- `optimize_description`: Phase 2 trigger optimization.

Use checkpointer:

- Same pattern as `builder_service.run_v3_message_stream` in `backend/app/services/builder_service.py:392`.
- Config thread id: `skill_builder_<session_id>`.

## Hidden Agent Build Function

Create `backend/app/agent_runtime/skill_builder/agent.py`.

Use the Assistant pattern from `backend/app/agent_runtime/assistant/assistant_agent.py:55`.

Public function:

```python
async def build_skill_builder_model(db: AsyncSession) -> BaseChatModel:
    resolved = await resolve_system_model(db, "text_primary")
    return create_chat_model(
        resolved.provider,
        resolved.model_name,
        api_key=resolved.api_key,
        base_url=resolved.base_url,
    )
```

Do not use user LLM credentials for this hidden builder in v1. It should behave like Builder/Assistant system functionality.

### System LLM Readiness

The first click on "대화로 만들기" must not become a 500 when the operator has not configured the System LLM.

Backend behavior:

- Catch `SystemModelNotConfiguredError` from `build_skill_builder_model(...)` and `resolve_system_model(db, "text_primary")`.
- Return `409 Conflict` with a structured error code `SYSTEM_LLM_NOT_CONFIGURED`.
- The response body should include only safe setup context:

```json
{
  "code": "SYSTEM_LLM_NOT_CONFIGURED",
  "message": "Skill Builder requires the text_primary system model to be configured.",
  "role": "text_primary"
}
```

- Record `skill_builder.system_model_missing` with outcome `denied` and metadata `{"role": "text_primary"}`.
- Do not create a `skill_builder_sessions` row when the model is missing and the request cannot proceed.
- Keep text skill creation and package upload unaffected.

Frontend behavior:

- In `SkillBuilderDialog`, show an inline unavailable state instead of an empty chat thread.
- For normal users, say that the Skill Builder model needs administrator setup and keep the Text and Package Upload tabs available.
- For `is_super_user`, include a compact link or action to the existing System LLM settings surface.
- Component tests must cover this error so future router/agent changes do not regress into a generic failure toast.
- E2E tests that need a configured System LLM can use the new `E2E_LLM_BASE_URL`, `E2E_LLM_API_KEY`, and `E2E_LLM_MODEL` seed path. Keep at least one test/mocked API path for missing System LLM so readiness behavior is still covered.

## Eval And Benchmark Phase

Claude Code's installed skill creator adds a strong eval loop. Moldy should implement this after the base builder flow is working.

Create `backend/app/agent_runtime/skill_builder/eval_runner.py`.
Keep script-backed deterministic cases in `backend/app/agent_runtime/skill_builder/deterministic_eval_execution.py` so `eval_runner.py` owns shared eval helpers and `deterministic_eval_runner.py` stays focused on aggregation/grader shaping.

Data shapes:

```python
Eval item:
{
  "id": 1,
  "prompt": "현실적인 사용자 요청",
  "expected_output": "성공 조건 설명",
  "files": [],
  "expectations": ["The output includes a table with owner, due date, and action item columns."]
}

Run result:
{
  "eval_id": 1,
  "configuration": "with_skill",
  "result": {
    "pass_rate": 0.75,
    "passed": 3,
    "failed": 1,
    "total": 4,
    "time_seconds": 18.2,
    "tokens": 4200,
    "tool_calls": 8,
    "errors": 0
  },
  "expectations": [
    {
      "text": "The output includes a table with owner, due date, and action item columns.",
      "passed": true,
      "evidence": "The result contains a Markdown table with columns 담당자, 마감일, and 액션 아이템."
    }
  ],
  "notes": []
}
```

Execution strategy:

- Materialize draft package into `data/skill_builder/<session_id>/iterations/<n>/skill/<slug>/`.
- With-skill run:
  - Mount the draft skill as the only selected skill.
  - Use a temporary thread id `skill_builder_eval_<session_id>_<eval_id>_with`.
  - Tell the evaluator agent to read `SKILL.md` before doing the task.
- Baseline run:
  - Same prompt.
  - No skill mounted.
  - Temporary thread id `skill_builder_eval_<session_id>_<eval_id>_baseline`.
- Save transcript and outputs under:

```text
data/skill_builder/<session_id>/iterations/<n>/<eval-name>/
├── with_skill/
│   ├── transcript.md
│   ├── outputs/
│   ├── metrics.json
│   └── grading.json
└── without_skill/
    ├── transcript.md
    ├── outputs/
    ├── metrics.json
    └── grading.json
```

Grading strategy:

- Use an internal grader prompt based on the local Claude Code `agents/grader.md`.
- The grader must inspect outputs, not only transcript claims.
- Each expectation is pass/fail with evidence.
- The grader also reports weak expectations when they do not discriminate real success.

Benchmark aggregation:

- Store aggregate in `session.eval_result`.
- Show pass rate, time, token, and error deltas in frontend.
- Do not block finalization on eval warnings. Block finalization only on validation errors.

## Trigger Description Optimization Phase

Implement after the eval runner.

Create:

- `backend/app/agent_runtime/skill_builder/trigger_eval.py`
- frontend panel in `frontend/src/components/skill/skill-builder-eval-panel.tsx`

Flow:

1. Generate 20 realistic trigger queries:
   - 8-10 should-trigger
   - 8-10 should-not-trigger
   - include near misses, casual language, typos, domain-adjacent requests, and ambiguous cases.
2. User reviews the set in the UI.
3. Run each query three times against a lightweight trigger classifier that sees only:
   - skill name
   - skill description
   - query
4. Record:
   - `trigger_rate`
   - expected should-trigger value
   - pass/fail under threshold `0.5`
5. Ask the hidden builder model to rewrite only the `description`.
6. Split eval set into train/test using a stable seed.
7. Pick best description by held-out test score.
8. Save before/after description and scores to `session.trigger_eval_result`.

Do not call external `claude -p` from the backend. Use Moldy's configured system model so the feature is provider-neutral.

## Frontend UX

Replace the current `ScratchTab` behavior in `frontend/src/components/skill/skill-create-dialog.tsx:255`.

New tabs:

- Text
- Package upload
- 대화로 만들기

The new conversational tab opens `SkillBuilderDialog` instead of creating a JSZip package in the browser. `SkillBuilderDialog` is the only chat-builder implementation in v1; do not duplicate the flow as a separate tab component.

Recommended UI layout:

```text
DialogShell size="xl"
┌──────────────────────────────────────────────┐
│ header: 스킬 만들기                           │
├───────────────────────┬──────────────────────┤
│ chat thread            │ preview panel         │
│ - user/assistant msgs  │ - file tree           │
│ - composer             │ - SKILL.md preview    │
│ - stream status        │ - validation issues   │
│                        │ - eval summary        │
├───────────────────────┴──────────────────────┤
│ footer: validate / run evals / create skill   │
└──────────────────────────────────────────────┘
```

Use product UI rules:

- Keep this as a tool surface, not a marketing hero.
- Use existing `DialogShell`, `FormFooter`, `Tabs`, `Textarea`, `Button`, and Moldy classes.
- Use lucide icons for actions: `Sparkles`, `Play`, `CheckCircle`, `AlertTriangle`, `FileText`, `Package`.
- All visible copy must go through `frontend/messages/ko.json` and `frontend/messages/en.json`.

Installed skill evaluation tab:

- Extend `frontend/src/components/skill/skill-detail-dialog.tsx` so package and text skills both use one detail shell that can show evaluation details.
- Render visible tabs from a `getVisibleSkillDetailTabs(skill, state, initialTab)` helper instead of hard-coding all advanced tabs.
- Always include the primary editing tab:
  - `Content` for text skills
  - `Files` for package skills
- Show `Credentials` when credential requirements exist, required credentials are missing, `initialTab === "credentials"`, or a user opens it from a health/action link.
- Show `Evaluation` when the skill has an evaluation set, latest evaluation summary, running evaluation, stale evaluation state, low-confidence state, `initialTab === "evaluation"`, or the user clicks "평가 관리".
- Show `History` when revisions exist, the skill has been changed after the feature shipped, `initialTab === "history"`, or the user clicks "변경 이력".
- Show `Metadata` under an overflow/more action for simple skills, and as a normal tab when marketplace/compatibility metadata exists.
- Recommended visible order when all apply:
  - `Content` or `Files`
  - `Credentials`
  - `Evaluation`
  - `History`
  - `Metadata`
- The `Evaluation` tab should be backed by `frontend/src/components/skill/skill-evaluation-tab.tsx`.
- The tab should show:
  - latest completed run summary: pass rate, trigger accuracy, average duration, token delta, and completion time
  - a stale badge when `latest.skill_content_hash !== skill.content_hash`
  - primary action: `평가 다시 실행` / `Run evaluation again`
  - secondary actions: create evaluation set, edit evaluation set, open run detail
  - left column: reusable evaluation sets and run history
  - right column: selected run detail with grouped case results, evidence, grader feedback, and benchmark deltas
- Rerunning an evaluation should run the selected `SkillEvaluationSet` against the current skill files and create a new `SkillEvaluationRun`.
- Builder-created evals become the first evaluation set on the finalized skill, so the user can rerun the same checks immediately from the skill detail dialog.
- The UI should treat evaluation as optional quality evidence. Do not block normal skill editing or installation because a skill has no evaluation yet.
- Empty states:
  - If no evaluation set exists, show a compact action row: `평가 기준 만들기`.
  - If no revision exists, show the legacy-skill History copy from the Skill Revision Retention And Backfill section.
  - If no credential requirement exists, do not show a blank Credentials tab in the default tab list.

Frontend API:

```typescript
export const skillBuilderApi = {
  start: (request: SkillBuilderStartRequest) =>
    apiFetch<SkillBuilderSession>("/api/skill-builder", {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getSession: (sessionId: string) =>
    apiFetch<SkillBuilderSession>(`/api/skill-builder/${sessionId}`),
  validate: (sessionId: string) =>
    apiFetch<SkillBuilderSession>(`/api/skill-builder/${sessionId}/validate`, {
      method: "POST",
    }),
  confirm: (sessionId: string) =>
    apiFetch<Skill>(`/api/skill-builder/${sessionId}/confirm`, {
      method: "POST",
    }),
  runEvals: (sessionId: string) =>
    apiFetch<SkillBuilderSession>(`/api/skill-builder/${sessionId}/evals/run`, {
      method: "POST",
    }),
};
```

Installed skill evaluation API:

```typescript
export const skillEvaluationsApi = {
  listSets: (skillId: string) =>
    apiFetch<SkillEvaluationSetSummary[]>(`/api/skills/${skillId}/evaluations`),
  createSet: (skillId: string, request: SkillEvaluationSetCreateRequest) =>
    apiFetch<SkillEvaluationSet>(`/api/skills/${skillId}/evaluations`, {
      method: "POST",
      body: JSON.stringify(request),
    }),
  getSet: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationSet>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}`,
    ),
  updateSet: (
    skillId: string,
    evaluationSetId: string,
    request: SkillEvaluationSetUpdateRequest,
  ) =>
    apiFetch<SkillEvaluationSet>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}`,
      {
        method: "PATCH",
        body: JSON.stringify(request),
      },
    ),
  deleteSet: (skillId: string, evaluationSetId: string) =>
    apiFetch<void>(`/api/skills/${skillId}/evaluations/${evaluationSetId}`, {
      method: "DELETE",
    }),
  listRuns: (skillId: string, evaluationSetId: string) =>
    apiFetch<SkillEvaluationRunSummary[]>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs`,
    ),
  estimateRun: (
    skillId: string,
    evaluationSetId: string,
    request?: SkillEvaluationRunRequest,
  ) =>
    apiFetch<SkillEvaluationRunEstimate>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/estimate`,
      {
        method: "POST",
        body: JSON.stringify(request ?? {}),
      },
    ),
  run: (
    skillId: string,
    evaluationSetId: string,
    request?: SkillEvaluationRunRequest,
  ) =>
    apiFetch<SkillEvaluationRun>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs`,
      {
        method: "POST",
        body: JSON.stringify(request ?? {}),
      },
    ),
  getRun: (skillId: string, evaluationSetId: string, runId: string) =>
    apiFetch<SkillEvaluationRun>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}`,
    ),
  cancelRun: (skillId: string, evaluationSetId: string, runId: string) =>
    apiFetch<SkillEvaluationRun>(
      `/api/skills/${skillId}/evaluations/${evaluationSetId}/runs/${runId}/cancel`,
      { method: "POST" },
    ),
};
```

Installed skill revision API:

```typescript
export const skillRevisionsApi = {
  list: (skillId: string) =>
    apiFetch<SkillRevisionSummary[]>(`/api/skills/${skillId}/revisions`),
  get: (skillId: string, revisionId: string) =>
    apiFetch<SkillRevisionDetail>(
      `/api/skills/${skillId}/revisions/${revisionId}`,
    ),
  rollback: (skillId: string, revisionId: string) =>
    apiFetch<SkillRollbackResponse>(
      `/api/skills/${skillId}/revisions/${revisionId}/rollback`,
      { method: "POST" },
    ),
};
```

Frontend SSE:

- Mirror `streamBuilderMessage` and `streamBuilderResume`.
- New helpers:
  - `frontend/src/lib/sse/stream-skill-builder-message.ts`
  - `frontend/src/lib/sse/stream-skill-builder-resume.ts`
- Do not reuse `frontend/src/lib/sse/stream-assistant.ts`; that helper posts to `/api/agents/{agent_id}/assistant/message` and `/assistant/message/resume` with assistant-specific thread IDs.
- Do not route these helpers through normal chat `useMoldyLangGraphStream(...)` or Agent Protocol `/stream/events`; Skill Builder v1 owns a product authoring stream and fetches persisted session state from `/api/skill-builder/{session_id}` after stream completion or reconnect.

Skill Builder SSE wire contract:

- Skill Builder v1 uses a builder-specific product SSE stream, not the normal Agent Protocol stream. The normal chat protocol emits SSE `event: message` with a protocol payload; do not parse Skill Builder streams through `@langchain/react` transport or send `builder_status` events through `/langgraph/threads/{thread_id}/stream/events`.
- Reuse existing standard event names from `backend/app/agent_runtime/event_names.py` when possible:
  - `message_start`
  - `content_delta`
  - `message_end`
  - `error`
  - `interrupt`
- Add builder-specific event payloads through `format_sse(...)`; keep event names stable because the frontend stream helpers switch on them:
  - `builder_status`: `{ "session_id": "...", "status": "drafting", "phase": "draft_package" }`
  - `builder_activity`: `{ "kind": "validation", "status": "running", "label": "Validating package" }`
  - `draft_package`: `{ "file_count": 4, "files": [{"path": "SKILL.md", "role": "skill"}] }`
  - `validation_result`: `{ "error_count": 0, "warning_count": 2, "issues": [...] }`
  - `compatibility_result`: `{ "targets": {"openai_codex": {"status": "pass"}} }`
  - `changelog_draft`: `{ "summary": "...", "items": [...] }`
  - `eval_result`: `{ "pass_rate": 0.86, "benchmark": {...} }`
- If a future migration moves Skill Builder onto the Agent Protocol endpoint, convert these domain events to protocol `custom` events shaped as `{ "name": "builder_status", "payload": {...} }`, keep shared activity kinds limited to the actual `RunActivityKind` union, and update `activity-model.ts` because the current reducer ignores builder-domain custom events.
- Never stream full draft file bodies through status/activity events. File contents are already in the session/draft fetch path and preview state.
- Frontend `stream-skill-builder-*` helpers should accept unknown events and surface them as non-fatal debug/activity entries during development, but known events above must have typed handlers and tests.

## Detailed Tasks

### Task 1: Add Backend Session Model And Migration

**Files:**

- Create: `backend/app/models/skill_builder_session.py`
- Create: `backend/app/models/skill_evaluation.py`
- Create: `backend/app/models/skill_revision.py`
- Create: `backend/app/schemas/skill_builder.py`
- Create: `backend/app/schemas/skill_revision.py`
- Create: `backend/alembic/versions/m64_skill_builder_sessions.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/skill.py`

- [x] Add the `SkillBuilderStatus` enum and response/request schemas exactly as defined in the Data Model section.
- [x] Add `SkillBuilderMode` and fields for `source_skill_id`, `base_skill_version`, `base_content_hash`, and `base_snapshot`.
- [x] Add the SQLAlchemy model with JSON fields for messages, intent, draft package, validation, compatibility, changelog, eval, and trigger eval results.
- [x] Add `SkillEvaluationSet` and `SkillEvaluationRun` models exactly as defined in the Persistent Skill Evaluation Models section.
- [x] Add `SkillRevision` exactly as defined in the Skill Revision History section.
- [x] Add nullable `current_revision_id` to `Skill`.
- [x] Add `cancellation_requested_at` and `cancellation_reason` to `SkillEvaluationRun`.
- [x] Include evaluation template/version fields, run estimate JSON, runner version, grader prompt version, and eval schema version in the migration.
- [x] Include `skill_revisions` table, indexes, unique constraint, and `skills.current_revision_id` in the migration.
- [x] Add backend settings for `SKILL_EVALUATION_ENABLED`, `SKILL_EVALUATION_MAX_CONCURRENT`, `SKILL_EVALUATION_QUEUE_MAX_SIZE`, `SKILL_EVALUATION_RUN_TIMEOUT_SECONDS`, and `SKILL_EVALUATION_CASE_TIMEOUT_SECONDS`.
- [x] Add the Alembic migration with `down_revision` set to the current output of `uv run alembic heads`. Use `"m63_chat_navigator_indexes"` only if it is still the single head at execution time.
- [x] Export `SkillBuilderSession`, `SkillEvaluationSet`, `SkillEvaluationRun`, and `SkillRevision` from `backend/app/models/__init__.py`.
- [x] Run:

```bash
cd backend
uv run alembic upgrade head
```

Expected: migration applies and creates `skill_builder_sessions`, `skill_evaluation_sets`, `skill_evaluation_runs`, `skill_revisions`, and `skills.current_revision_id`.

- [x] Run:

```bash
cd backend
uv run pytest tests/test_storage_path_relative_invariant.py -q
```

Expected: existing storage-path tests still pass.

### Task 2: Add Draft Package Builder

**Files:**

- Create: `backend/app/skills/package_builder.py`
- Create: `backend/app/skills/package_hash.py`
- Create: `backend/tests/test_skill_builder_package_builder.py`
- Create: `backend/tests/test_skill_package_hash.py`

- [x] Implement `normalize_draft_path` and `build_skill_zip_bytes` from the Draft Package Builder section.
- [x] Implement `compute_package_tree_hash` from the Package Skill Content Hash Invariant section.
- [x] Add tests:
  - [x] valid draft package imports through `extract_package`
  - [x] missing `SKILL.md` raises `ValueError`
  - [x] `../secret.txt` raises `ValueError`
  - [x] `evals/evals.json` is excluded by default
  - [x] `evals/evals.json` is included when `include_evals=True`
  - [x] deterministic package tree hash is stable for identical bytes
  - [x] changing `SKILL.md`, `scripts/`, `references/`, or `agents/*.yaml` changes the package tree hash
  - [x] symlink or non-regular package entries fail closed
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_package_builder.py tests/test_skill_package_hash.py -q
```

Expected: all new package builder tests pass.

### Task 3: Add Skill Validator

**Files:**

- Create: `backend/app/skills/validator.py`
- Create: `backend/app/skills/compatibility.py`
- Create: `backend/tests/test_skill_builder_validator.py`
- Create: `backend/tests/test_skill_compatibility.py`

- [x] Implement validation rules from the Skill Validator section.
- [x] Use `parse_skill_md` for metadata parsing.
- [x] Use a temporary directory and `scan_package()` for secret scanning.
- [x] Validate `agents/moldy.yaml.credential_requirements` against the Credential-Aware Sandbox And Audit Policy section.
- [x] Validate each `definition_key` by importing `app.credentials.definitions` and checking `app.credentials.registry.registry`.
- [x] Validate `env_map` direction as `{credential_field_name: env_var_name}` and reject entries that map env vars to field names.
- [x] Warn when scripts or `SKILL.md` instructions use `curl` or obvious network URLs without `execution_profile.requires_network: true`.
- [x] Implement `check_portable_compatibility(draft_package)` in `backend/app/skills/compatibility.py`.
- [x] Include compatibility result in validation output with per-target `status`, `issues`, and aggregate counts.
- [x] Return structured issues with `code`, `severity`, `path`, and `message`.
- [x] Add tests:
  - [x] missing `SKILL.md` is an error
  - [x] missing frontmatter description is an error
  - [x] weak trigger description is a warning
  - [x] body copied from the current scratch HTML comment is a warning
  - [x] `references/` without a mention from `SKILL.md` is a warning
  - [x] secret-looking content is an error
  - [x] unknown credential `definition_key` is an error
  - [x] reversed `env_map` shape is an error
  - [x] `env_map` field not listed in `fields` is an error
  - [x] invalid env var name in `env_map` is an error
  - [x] network command without `execution_profile.requires_network` is a warning
  - [x] Moldy-only frontmatter in `SKILL.md` is a compatibility error
  - [x] missing `agents/openai.yaml` is a compatibility warning for generated skills
  - [x] absolute local paths in `SKILL.md` are compatibility warnings
  - [x] generated changelog text inside `SKILL.md` is a compatibility warning
  - [x] valid portable package returns `valid=True`
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_validator.py tests/test_skill_compatibility.py -q
```

Expected: all validator tests pass.

### Task 4: Add Skill Builder Service

**Files:**

- Create: `backend/app/services/skill_builder_service.py`
- Create: `backend/app/services/skill_builder_confirmation.py`
- Create: `backend/app/services/skill_builder_errors.py`
- Create: `backend/app/services/skill_evaluation_service.py`
- Create: `backend/app/services/skill_health_service.py`
- Create: `backend/app/services/skill_revision_service.py`
- Create: `backend/app/services/skill_revision_storage.py`
- Create: `backend/scripts/backfill_skill_revisions.py`
- Extend: `backend/app/routers/skills.py`
- Extend: `backend/app/skills/service.py`
- Create: `backend/tests/test_skill_builder_service.py`
- Create: `backend/tests/test_skill_builder_confirmation.py`
- Create: `backend/tests/test_skill_evaluation_service.py`
- Create: `backend/tests/test_skill_health_service.py`
- Create: `backend/tests/test_skill_revision_service.py`
- Create: `backend/tests/test_skill_revision_backfill.py`
- Extend: `backend/tests/test_skill_revision_mutations_api.py`
- Create: `backend/tests/test_skill_builder_audit.py`

- [x] Implement session CRUD.
- [x] Implement create-mode and improve-mode session creation.
- [x] Implement `load_skill_snapshot` for both text and package skills.
- [x] Implement reusable evaluation set/run CRUD in `skill_evaluation_service.py`.
- [x] Implement `estimate_run` and `cancel_run` in `skill_evaluation_service.py`.
- [x] Implement Skill Health calculation in `skill_health_service.py` with states from the Skill Health Summary section.
- [x] Implement skill revision snapshot zip writing in `skill_revision_storage.py`.
- [x] Implement `create_revision_for_skill`, `list_revisions`, and `get_revision` in `skill_revision_service.py`.
- [x] Implement `rollback_to_revision` in `skill_revision_service.py`.
- [x] Implement revision retention constants and pruning safeguards from the Skill Revision Retention And Backfill section.
- [x] Implement a rerunnable baseline backfill script for existing skills with no revisions.
- [x] Centralize package tree hashing in `compute_package_tree_hash`.
- [x] Ensure package upload and package file create/update/delete refresh `skill.content_hash`.
- [x] Ensure whole-package replacement and rollback refresh `skill.content_hash` before revision creation and evaluation staleness checks.
- [x] Ensure create-mode builder confirm refreshes `skill.content_hash` before revision creation and evaluation staleness checks.
- [x] Ensure improve-mode builder confirm refreshes `skill.content_hash` before revision creation and evaluation staleness checks.
- [x] Create initial revision after builder create.
- [x] Create initial revision after text skill create and package upload.
- [x] Create new revisions after text content update, package file update/delete/upload, metadata update, builder improvement, and rollback.
- [x] Generate changelog summaries from builder diffs and store them on the resulting revision row.
- [x] Keep changelog summaries out of `SKILL.md`; use them as default marketplace release-note text later.
- [x] Implement `append_message` with a JSON array shape:

```json
[
  {
    "role": "user",
    "content": "만들고 싶은 스킬 설명",
    "created_at": "2026-06-13T00:00:00Z"
  },
  {
    "role": "assistant",
    "content": "질문 또는 요약",
    "created_at": "2026-06-13T00:00:01Z"
  }
]
```

- [x] Implement `save_draft_package` and `save_validation_result`.
- [x] Implement `claim_for_confirming` using the same atomic update pattern as `builder_service.claim_for_confirming`.
- [x] Implement `confirm_session` using the confirm flow in this plan.
- [x] Return non-secret audit metadata from confirm/apply helpers: session id, mode, source skill id, file counts, changed counts, credential requirement count, old hash, and new hash.
- [x] In improve mode, apply confirmed changes to the existing skill only when `base_content_hash` still matches the current skill hash.
- [x] During confirm, convert `evals/evals.json` and `session.eval_result` into `SkillEvaluationSet` and `SkillEvaluationRun` rows linked to the finalized skill.
- [x] Add tests:
  - [x] session ownership is enforced by query helper
  - [x] confirm refuses sessions with validation errors
  - [x] confirm is idempotent after completion
  - [x] confirm stores `credential_requirements` and `execution_profile` on `Skill`
  - [x] confirm creates package-kind skill with `origin_kind="created_by_me"` and `source_kind="user"`
  - [x] improve session stores `source_skill_id`, base version, base content hash, and base snapshot
  - [x] improve confirm updates an existing text skill
  - [x] improve confirm updates changed package files and preserves unchanged files
  - [x] improve confirm returns 409 when the current skill hash differs from `base_content_hash`
  - [x] confirm creates an evaluation set when the draft contains evals
  - [x] confirm copies builder-time eval results into a skill evaluation run
  - [x] evaluation run snapshots `skill.version` and `skill.content_hash`
  - [x] package file update changes `skill.content_hash`
  - [x] package file delete changes `skill.content_hash`
  - [x] identical package file write keeps the same `skill.content_hash`
  - [x] package content hash change makes the latest completed evaluation stale by comparison
  - [x] evaluation run snapshots runner/grader/schema versions
  - [x] `estimate_run` returns no DB row and includes case count, model call count, timeout, and approximate cost
  - [x] `cancel_run` transitions queued/running/grading to cancelled and rejects completed runs
  - [x] health state returns `needs_rerun` when content hash differs
  - [x] confirm audit metadata excludes prompt text, generated file content, stdout/stderr, and credential values
  - [x] `create_revision_for_skill` writes a zip snapshot and increments `revision_number`
  - [x] `rollback_to_revision` creates a new rollback revision and updates the skill row
  - [x] rollback does not mutate the restored-from revision
  - [x] revision changelog is stored on `SkillRevision`, not in `SKILL.md`
  - [x] backfill creates a baseline revision for a legacy skill and is idempotent
  - [x] first mutation of a legacy skill creates a baseline revision before the mutation revision
  - [x] builder-created package skills still accept existing package file update/upload/delete APIs and create file-update revisions
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_service.py tests/test_skill_evaluation_service.py tests/test_skill_health_service.py tests/test_skill_revision_service.py tests/test_skill_revision_backfill.py tests/test_skill_revision_mutations_api.py tests/test_skill_builder_audit.py tests/test_skill_package_hash.py -q
```

Expected: service tests pass.

### Task 5: Add Skill Builder Router

**Files:**

- Create: `backend/app/routers/skill_builder.py`
- Create: `backend/app/routers/skill_builder_support.py`
- Create: `backend/app/routers/skill_revisions.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_skill_builder_api.py`
- Create: `backend/tests/test_skill_revisions_api.py`

- [x] Add REST endpoints from the API Contract section.
- [x] Add revision endpoints from the API Contract section.
- [x] Add SSE endpoints using the same `StreamingResponse` headers as `backend/app/routers/builder.py:153`.
- [x] Include CSRF dependency on mutating endpoints.
- [x] Convert missing `text_primary` System LLM setup into `409 SYSTEM_LLM_NOT_CONFIGURED` for builder start/message/eval paths that need the hidden model.
- [x] Record `skill_builder.session_create` when `POST /api/skill-builder` succeeds.
- [x] Record `skill_builder.system_model_missing` with outcome `denied` when System LLM readiness blocks the builder.
- [x] Record `skill_builder.validation_failed` when `POST /validate` persists error-level issues.
- [x] Record `skill_builder.secret_scan_blocked` when validation or confirm blocks a draft because `scan_package()` found secrets.
- [x] Record `skill_builder.confirm_create` when create-mode confirm creates a new skill.
- [x] Record `skill_builder.apply_improvement` when improve-mode confirm updates the existing skill.
- [x] Record `skill_builder.apply_conflict` with outcome `denied` when improve-mode hash conflict returns 409.
- [x] Record `skill_revision.create` whenever a revision snapshot is created by service calls in this feature.
- [x] Record `skill_revision.rollback` when rollback succeeds.
- [x] Use `audit_service.record_event(...)` and pass `request` so request id, IP, and user-agent match existing audit behavior.
- [x] Add route include in `backend/app/main.py`.
- [x] Add API tests:
  - [x] `POST /api/skill-builder` creates a create-mode session
  - [x] `POST /api/skill-builder` returns `409 SYSTEM_LLM_NOT_CONFIGURED` when `text_primary` is not configured
  - [x] `POST /api/skill-builder` with `mode="improve"` creates a session from an owned source skill
  - [x] `POST /api/skill-builder` with `mode="improve"` returns 404 for another user's skill
  - [x] `GET /api/skill-builder/{id}` returns only owned sessions
  - [x] `POST /validate` persists validation result
  - [x] `POST /confirm` returns `SkillResponse`
  - [x] `POST /confirm` returns 409 for improve-mode hash conflict
  - [x] cross-user session access returns 404
  - [x] session create writes `skill_builder.session_create`
  - [x] System LLM readiness failure writes `skill_builder.system_model_missing` without creating a session
  - [x] confirm create writes `skill_builder.confirm_create`
  - [x] improve apply writes `skill_builder.apply_improvement`
  - [x] improve conflict writes `skill_builder.apply_conflict` with no file content in metadata
  - [x] `GET /api/skills/{skill_id}/revisions` lists only owned skill revisions
  - [x] `GET /api/skills/{skill_id}/revisions/{revision_id}` returns changelog and compatibility metadata
  - [x] `POST /api/skills/{skill_id}/revisions/{revision_id}/rollback` creates a new rollback revision
  - [x] rollback on another user's skill returns 404
  - [x] rollback writes `skill_revision.rollback` without file bodies in metadata
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_api.py tests/test_skill_builder_api_contracts.py tests/test_skill_revisions_api.py tests/test_skill_builder_audit.py -q
```

Expected: API tests pass.

### Task 6: Add LangGraph Hidden Builder

**Files:**

- Create: `backend/app/agent_runtime/skill_builder/__init__.py`
- Create: `backend/app/agent_runtime/skill_builder/state.py`
- Create: `backend/app/agent_runtime/skill_builder/graph.py`
- Create: `backend/app/agent_runtime/skill_builder/agent.py`
- Create: `backend/app/agent_runtime/skill_builder/deep_agent_worker.py`
- Create: `backend/app/agent_runtime/skill_builder/prompt.md`
- Create: `backend/app/services/skill_builder_workflow.py`
- Modify: `backend/app/routers/skill_builder.py`
- Modify: `backend/app/services/skill_builder_service.py`

- [x] Implement graph state from the LangGraph section.
- [x] Implement model construction from the Hidden Agent Build Function section.
- [x] Let `SystemModelNotConfiguredError` bubble as a typed readiness error for routers/services to convert to `SYSTEM_LLM_NOT_CONFIGURED`, not as an unhandled exception.
- [x] Implement the optional Deep Agent draft worker from the Deep Agents Alignment section, using sandboxed draft storage rather than unrestricted `FilesystemBackend`.
- [x] Implement graph nodes:
  - [x] load existing skill snapshot when `mode="improve"`
  - [x] collect intent
  - [x] draft package
  - [x] validate package
  - [x] check compatibility
  - [x] generate changelog for improve mode
  - [x] apply user feedback
  - [x] review response
- [x] Persist draft package, validation result, compatibility result, and changelog draft after each draft or revision.
- [x] Ensure generated changelog is shown in the builder review but not written into `SKILL.md`.
- [x] Emit builder-specific SSE events with `format_sse` and shared parse/resume utilities where practical.
- [x] Emit the exact Skill Builder SSE wire events from the Frontend SSE section and add backend tests for event names and redacted payload shapes.
- [x] Align builder stream status/activity payloads with the LangGraph v3 compatibility section: use shared statuses `pending`, `running`, `requires_action`, `complete`, `error`, `cancelled`; keep builder-domain phases such as `validation`, `compatibility`, `evaluation`, and `revision` in `phase` or `data.domain` unless a shared activity-kind addition is intentionally implemented.
- [x] Do not route Skill Builder v1 through `conversation_agent_protocol` or create normal `ConversationRun` rows; `skill_builder_sessions` remains the source of truth.
- [x] Use `thread_id = f"skill_builder_{session_id}"`.
- [x] Ensure Deep Agent subagents used for grading/analyzing receive explicit skills and complete instructions. V1 does not wire custom Deep Agent subagents; if added later, they must be passed explicit skills and complete one-shot instructions.
- [x] Ensure builder telemetry and audit calls store phase/status/ids only, not prompt text, generated answer text, or draft file bodies.
- [x] Add a backend integration test with a fake chat model that produces a deterministic draft package.
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_hidden_graph.py tests/test_skill_builder_api.py tests/test_skill_builder_service.py -q
```

Expected: session, message, validation, and confirm paths pass with deterministic model stubs.

### Task 7: Add Eval Runner

**Files:**

- Create: `backend/app/agent_runtime/skill_builder/eval_runner.py`
- Create: `backend/app/agent_runtime/skill_builder/deterministic_eval_execution.py`
- Create: `backend/app/agent_runtime/skill_builder/eval_templates.py`
- Create: `backend/app/services/skill_builder_eval_service.py`
- Create: `backend/app/services/skill_evaluation_worker.py`
- Create: `backend/app/routers/skill_builder_evals.py`
- Extend: `backend/app/schemas/skill_builder.py`
- Extend: `backend/app/services/skill_builder_service.py`
- Extend: `backend/app/routers/skill_builder.py`
- Modify: `backend/app/router_registry.py`
- Modify: `backend/app/marketplace/skill_runtime.py`
- Modify: `backend/app/agent_runtime/skill_executor.py`
- Create: `backend/app/agent_runtime/skill_execution_policy.py`
- Create: `backend/tests/test_skill_builder_eval_runner.py`
- Create: `backend/tests/test_skill_builder_eval_api.py`
- Create: `backend/tests/test_skill_builder_eval_templates.py`
- Create: `backend/tests/test_skill_evaluation_worker.py`
- Create: `backend/tests/test_skill_executor_credential_audit.py`
- Create: `backend/tests/test_skill_executor_sandbox_denials.py`

- [x] Implement eval schema support for `evals/evals.json`.
- [x] Implement internal template selection from the Internal Evaluation Templates section.
- [x] Implement the bounded worker/queue behavior from the Evaluation Execution Infrastructure section.
- [x] Generate 2-3 realistic eval prompts from intent when the user asks to test.
- [x] Do not expose evaluation template selection as a default user-facing picker.
- [x] Allow optional eval-case review only for advanced or low-confidence flows.
- [x] Add eval runner output directory contract for with-skill and without-skill runs.
- [x] Run with-skill and without-skill configurations into separate output folders.
- [x] Use the current selected-skill runtime mount pattern for with-skill evaluation runs.
- [x] Reuse or refactor the `execute_in_skill` command policy instead of adding another subprocess parser.
- [x] Enforce timeout, selected-skill path validation, credential redaction, and output directory scoping for eval scripts.
- [x] Refuse eval execution that needs required user credentials when bindings are missing.
- [x] Return `SYSTEM_LLM_NOT_CONFIGURED` before creating execution artifacts when grader/model-based evaluation lacks the system model.
- [x] Refuse eval network execution when the skill uses `curl` or external URLs but lacks `execution_profile.requires_network: true`.
- [x] Extend `SkillToolContext` with non-secret audit correlation fields needed by `execute_in_skill`: user id, agent id, thread id, and optional run id.
- [x] Add best-effort credential-use audit writes in `execute_in_skill` for each unique injected user credential.
- [x] The credential audit metadata must include only `kind`, skill id/slug, requirement key, agent id, thread id, run id, command executable, and timeout seconds.
- [x] Implement grader result format with `expectations`, `summary`, `execution_metrics`, `timing`, `claims`, and `eval_feedback`.
- [x] Implement initial benchmark aggregation with pass-rate, mean-score, and delta.
- [x] Extend benchmark aggregation with stddev/min/max.
- [x] Persist the aggregate to `session.eval_result`.
- [x] Add tests:
  - [x] eval runner creates with-skill and without-skill output directories
  - [x] template selection chooses `structured_extraction` for action-item/table skills
  - [x] template selection chooses `research` for citation/source skills
  - [x] grader fails weak/missing output evidence
  - [x] benchmark aggregation computes pass rate and delta
  - [x] eval runner refuses missing credential bindings before creating execution artifacts
  - [x] eval runner refuses missing system model before creating execution artifacts
  - [x] eval runner refuses undeclared network execution
  - [x] worker enforces max concurrency and queue-full behavior
  - [x] worker cancellation moves queued runs directly to cancelled and running runs through cooperative cancellation
  - [x] startup reconciliation marks stale running/grading rows interrupted
  - [x] credential audit rows are written when `execute_in_skill` injects a bound credential
  - [x] credential audit metadata does not contain decrypted values, stdout, stderr, or raw command arguments
- [x] Add regression coverage for `execute_in_skill` credential audit rows and sanitized metadata.
- [x] Add regression coverage for undeclared `curl` execution denial and sanitized `skill_security.sandbox_denied` metadata.
- [x] Add regression coverage for internal eval template selection: structured extraction, research, and general task fallback.
- [x] Add regression coverage for `evals/evals.json` parser validation, top-level list shorthand, and malformed JSON.
- [x] Add regression coverage for deterministic eval case generation from structured extraction, research, and general templates.
- [x] Add regression coverage for eval runner output directories and initial benchmark aggregation.
- [x] Add regression coverage for worker max concurrency enforcement.
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_eval_runner.py tests/test_skill_builder_eval_api.py tests/test_skill_builder_eval_templates.py tests/test_skill_evaluation_worker.py tests/test_skill_executor_credential_audit.py tests/test_skill_executor_sandbox_denials.py -q
```

Expected: eval runner tests pass.

- [x] Run targeted sandbox parity regression checks:

```bash
cd backend
uv run ruff check app/agent_runtime/skill_builder/deterministic_eval_runner.py app/agent_runtime/skill_builder/deterministic_eval_execution.py app/agent_runtime/skill_builder/eval_runner.py tests/test_skill_evaluation_worker.py tests/test_skill_evaluation_runtime_context.py
uv run pytest tests/test_skill_evaluation_worker.py tests/test_skill_builder_eval_runner.py tests/test_skill_evaluation_runtime_context.py tests/test_skill_evaluation_worker_audit.py tests/test_skill_evaluation_worker_cancellation.py tests/test_skill_executor_credential_audit.py tests/test_skill_executor_sandbox_denials.py -q
```

Expected: evaluation runner stays on the same selected-skill mount, command policy, output dir, credential redaction, and audit path as `execute_in_skill`.

### Task 8: Add Installed Skill Evaluation API

**Files:**

- Create: `backend/app/schemas/skill_evaluation.py`
- Create: `backend/app/routers/skill_evaluations.py`
- Extend: `backend/app/services/skill_evaluation_service.py`
- Extend: `backend/app/services/skill_evaluation_worker.py`
- Extend: `backend/app/services/skill_health_service.py`
- Create: `backend/app/services/skill_response_enrichment.py`
- Modify: `backend/app/main.py`
- Extend: `backend/app/schemas/skill.py`
- Extend: `backend/app/skills/service.py`
- Create: `backend/tests/test_skill_evaluations_api.py`
- Create: `backend/tests/test_skill_health_service.py`
- Create: `backend/tests/test_skill_evaluation_audit.py`
- Create: `backend/tests/test_skill_evaluation_worker.py`
- Create: `backend/tests/test_skill_evaluation_quality_api.py`
- Extend: `backend/tests/test_skills_api_regression.py`

- [x] Add response/request schemas for evaluation sets, run summaries, run detail, and latest evaluation summary.
- [x] Add `/api/skills/{skill_id}/evaluations` endpoints from the API Contract section.
- [x] Add `/estimate` and `/runs/{run_id}/cancel` endpoints.
- [x] Make `POST /runs` commit a queued run and enqueue it through `SkillEvaluationWorker`; do not execute the full evaluation inside the request handler.
- [x] Register `SkillEvaluationWorker` in app lifespan with startup interrupted-run reconciliation and shutdown stop.
- [x] Persist installed-skill eval runs in `skill_evaluation_runs`, not normal `conversation_runs`.
- [x] Enforce ownership by loading the parent skill through `skill_service.get_skill`.
- [x] Add CSRF dependency to create/update/delete/run endpoints.
- [x] Populate `latest_evaluation_summary` in skill list and detail responses without N+1 queries.
- [x] Populate `health` in skill list and detail responses.
- [x] Implement run creation so it snapshots the current `skill.version` and `skill.content_hash`.
- [x] Implement initial queued/running/grading/completed/failed/cancelled transitions through the worker, including cancellation field checks before completion.
- [x] Complete cooperative cancellation checkpoints inside the full eval runner cases, baseline runs, grading, subprocess timeout, and aggregation phases.
- [x] Mark previous runs stale in UI by hash comparison; do not mutate old rows just to represent stale status.
- [x] Before run creation, call `missing_required_keys(...)` or the same resolution path used by runtime; return `MARKETPLACE_CREDENTIAL_REQUIRED` if required user bindings are missing.
- [x] Record `skill_evaluation.credential_missing` with outcome `denied` when run creation is blocked by missing credentials.
- [x] Record `skill_evaluation.run_create`, `skill_evaluation.run_start`, `skill_evaluation.run_complete`, `skill_evaluation.run_fail`, and `skill_evaluation.run_cancel` with sanitized metadata.
- [x] Record `skill_security.sandbox_denied` when the eval runner blocks undeclared network access before launch.
- [x] Extend `skill_security.sandbox_denied` coverage to unsupported executables, path traversal, and timeout policy violations before launch.
- [x] Add API tests:
  - [x] list returns evaluation sets owned by the skill owner
  - [x] create set persists eval prompts and expectations
  - [x] estimate returns case count, model calls, approximate cost, and timeout
  - [x] rerun creates a new `SkillEvaluationRun`
  - [x] rerun enqueues the run instead of executing the full evaluation inside the HTTP request
  - [x] queue-full returns `SKILL_EVALUATION_QUEUE_FULL`
  - [x] missing System LLM returns `SYSTEM_LLM_NOT_CONFIGURED` before a run row is created
  - [x] cancel transitions an active run to `cancelled`
  - [x] cancel sets `cancellation_requested_at` for running/grading runs
  - [x] stale detection works when skill content hash changes after a run
  - [x] skill detail response includes `health`
  - [x] cross-user access returns 404
  - [x] missing required skill credential binding returns `MARKETPLACE_CREDENTIAL_REQUIRED` and no run row is created
  - [x] run create/cancel/complete/failure audit events contain IDs and summary metrics but no prompts or outputs
  - [x] sandbox denial audit event contains reason code and executable only, not raw command arguments
- [x] Add regression coverage for run enqueue, queue-full rollback, background queue consumption, worker complete/fail transitions, cancelled-run skip, and interrupted running/grading reconciliation.
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_evaluations_api.py tests/test_skill_evaluation_service.py tests/test_skill_evaluation_worker.py tests/test_skill_health_service.py tests/test_skill_evaluation_quality_api.py tests/test_skill_evaluation_audit.py -q
```

Expected: installed skill evaluation APIs pass and existing skill serialization still works.

### Task 9: Add Trigger Description Optimization

**Files:**

- Create: `backend/app/agent_runtime/skill_builder/trigger_eval.py`
- Extend: `backend/app/services/skill_builder_service.py`
- Create: `backend/app/routers/skill_builder_trigger.py`
- Modify: `backend/app/router_registry.py`
- Create: `backend/tests/test_skill_builder_trigger_eval.py`

- [x] Generate should-trigger and should-not-trigger query sets.
- [x] Use a deterministic train/test split with seed `42`.
- [x] Implement trigger classifier over skill name, description, and query.
- [x] Run each query three times and calculate `trigger_rate`.
- [x] Rewrite description with the system model, keeping it under 1024 characters.
- [x] Pick the best description by held-out test score.
- [x] Persist before/after and scores to `session.trigger_eval_result`.
- [x] Add tests:
  - [x] train/test split preserves at least one positive and one negative in test set
  - [x] rewritten description under 1024 chars is accepted
  - [x] best description is selected by test score, not train score
- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_trigger_eval.py -q
```

Expected: trigger optimization tests pass.

### Task 10: Add Frontend Types, API, Hooks, And SSE Helpers

**Files:**

- Create: `frontend/src/lib/types/skill-builder.ts`
- Create: `frontend/src/lib/types/skill-evaluation.ts`
- Create: `frontend/src/lib/types/skill-revision.ts`
- Create: `frontend/src/lib/api/skill-builder.ts`
- Create: `frontend/src/lib/api/skill-evaluations.ts`
- Create: `frontend/src/lib/api/skill-revisions.ts`
- Create: `frontend/src/lib/hooks/use-skill-builder.ts`
- Create: `frontend/src/lib/hooks/use-skill-evaluations.ts`
- Create: `frontend/src/lib/hooks/use-skill-revisions.ts`
- Create: `frontend/src/lib/sse/stream-skill-builder-message.ts` with message and resume helpers.

- [x] Mirror backend session, draft package, file, validation issue, eval, and benchmark shapes in TypeScript.
- [x] Include `compatibility_result` and `changelog_draft` in `skill-builder.ts`.
- [x] Include `SkillBuilderMode`, `source_skill_id`, base version/hash, and base snapshot fields in `skill-builder.ts`.
- [x] Mirror installed skill evaluation set/run/latest summary shapes in TypeScript.
- [x] Mirror `SkillRevisionSummary`, `SkillRevisionDetail`, and `SkillRollbackResponse` in `skill-revision.ts`.
- [x] Add `SkillHealthSummary` and `SkillEvaluationRunEstimate` TypeScript types.
- [x] Add TanStack Query hooks currently backed by implemented APIs:
  - `useSkillBuilderSession`
  - `useStartSkillBuilder`
  - `useValidateSkillBuilderSession`
  - `useConfirmSkillBuilderSession`
  - `useSkillEvaluationSets`
  - `useSkillEvaluationRuns`
  - `useEstimateSkillEvaluationRun`
  - `useCreateSkillEvaluationRun`
  - `useCancelSkillEvaluationRun`
  - `useSkillRevisions`
  - `useSkillRevision`
  - `useRollbackSkillRevision`
- [x] Add the builder-session eval run hook after `POST /api/skill-builder/{session_id}/evals/run` is implemented.
- [x] Add builder-specific stream helpers using shared SSE parsing/resume behavior where practical.
- [x] Keep `stream-skill-builder-message.ts` limited to `/api/skill-builder/{session_id}/messages` and `/messages/resume`; do not import `streamAssistant`, `useMoldyLangGraphStream`, `useChatRuntime`, or `ChatRuntimeSection`.
- [x] Add typed handlers for `builder_status`, `builder_activity`, `draft_package`, `validation_result`, `compatibility_result`, `changelog_draft`, and `eval_result`.
- [x] Do not depend on legacy normal chat `useChatRuntime`; current `main` defaults normal chat to `langgraph_v3`.
- [x] Add tests with `NEXT_PUBLIC_CHAT_RUNTIME` unset so the default `langgraph_v3` app shell does not accidentally break Skill Builder.
- [x] Invalidate `['skills']` after confirm succeeds.
- [x] In improve mode, invalidate `['skills']`, `['skills', id]`, `['skills', id, 'files']`, and `['skills', id, 'content']` after confirm succeeds.
- [x] Invalidate skill evaluation and skill detail queries after a rerun succeeds.
- [x] Invalidate `['skills']`, `['skills', id]`, `['skills', id, 'files']`, `['skills', id, 'content']`, evaluation queries, and revision queries after rollback succeeds.
- [x] Run:

```bash
cd frontend
pnpm lint
```

Expected: lint passes or reports only existing unrelated issues. Fix new issues from these files.

### Task 11: Replace Scratch Tab With Conversational Builder

**Files:**

- Modify: `frontend/src/components/skill/skill-create-dialog.tsx`
- Create: `frontend/src/components/skill/skill-builder-dialog.tsx`
- Create: `frontend/src/components/skill/skill-builder-chat.tsx`
- Create: `frontend/src/components/skill/skill-builder-preview.tsx`
- Create: `frontend/src/components/skill/skill-builder-validation.tsx`
- Create: `frontend/src/components/skill/skill-builder-eval-panel.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-builder-preview.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-builder-dialog.test.tsx`
- Modify: `frontend/src/app/skills/page.tsx`
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`

- [x] Change `CreateTab` to `'chat' | 'text' | 'package'`.
- [x] Change `/skills` primary CTA and empty state action to open `openCreate('chat')`.
- [x] Rename the visible scratch tab copy to "대화로 만들기" / "Build by chat".
- [x] Remove browser-side JSZip package creation from `ScratchTab`.
- [x] Start a skill builder session from the user's initial request.
- [x] Render `SYSTEM_LLM_NOT_CONFIGURED` as the System LLM readiness state described in the System LLM Readiness section, not as a generic error toast.
- [x] Keep Text and Package Upload tabs usable when conversational builder is unavailable.
- [x] Support `mode="create"` and `mode="improve"` props in `SkillBuilderDialog`.
- [x] Keep `SkillBuilderDialog` outside normal `ChatRuntimeSection`; it is a skill authoring workflow, not a normal Agent conversation surface in v1.
- [x] In improve mode, pass `source_skill_id` and use `applyImprovement` button copy.
- [x] Show a two-column chat + preview layout.
- [x] Render file tree preview from `draft_package.files`.
- [x] In improve mode, render original vs proposed file summary and changed/added/deleted counts.
- [x] Render generated changelog summary and items before the apply button in improve mode.
- [x] Render validation issues grouped by severity.
- [x] Render portable compatibility result with target chips for OpenAI/Codex, Claude Code, and Vercel Agent Skills.
- [x] Render eval benchmark when `draft_package.benchmark` or `session.eval_result` exists.
- [x] Confirm creates a skill and calls `onCreated(created.id, { openTab })` so the existing detail dialog can open on `evaluation` when evals exist.
- [x] Add component tests for the System LLM readiness state, including normal-user and super-user copy variants.
- [x] Add i18n messages in both Korean and English.
- [x] Run:

```bash
cd frontend
pnpm lint:i18n
pnpm lint:design-system
```

Expected: both pass. Fix any new copy or design-system violations.

### Task 12: Update Installed Skill Screens

**Files:**

- Modify: `frontend/src/app/skills/page.tsx`
- Modify: `frontend/src/components/skill/skill-detail-dialog.tsx`
- Modify: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog.tsx`
- Modify: `frontend/src/lib/types/skill.ts`
- Create: `frontend/src/components/skill/skill-card.tsx`
- Create: `frontend/src/components/skill/skill-page-dialogs.tsx`
- Create: `frontend/src/components/skill/skill-quality-inline.tsx`
- Create: `frontend/src/components/skill/use-skill-file-remote-cache.ts`
- Create: `frontend/src/components/skill/skill-detail-file-utils.ts`
- Create: `frontend/src/components/skill/skill-detail-text-editor.tsx`
- Create: `frontend/src/components/skill/skill-detail-package-editor.tsx`
- Create: `frontend/src/components/skill/skill-detail-package-sidebar.tsx`
- Create: `frontend/src/components/skill/skill-detail-package-footer.tsx`
- Create: `frontend/src/components/skill/skill-file-editor-pane.tsx`
- Create: `frontend/src/components/skill/skill-credential-bindings-panel.tsx`
- Create: `frontend/src/components/skill/skill-detail-tabs.tsx`
- Create: `frontend/src/components/skill/skill-summary-strip.tsx`
- Create: `frontend/src/components/skill/skill-evaluation-summary-badge.tsx`
- Create: `frontend/src/components/skill/skill-health-badge.tsx`
- Create: `frontend/src/components/skill/skill-evaluation-tab.tsx`
- Create: `frontend/src/components/skill/skill-credentials-tab.tsx`
- Create: `frontend/src/components/skill/skill-metadata-tab.tsx`
- Create: `frontend/src/components/skill/skill-evaluation-run-detail.tsx`
- Create: `frontend/src/components/skill/skill-history-tab.tsx`
- Create: `frontend/src/components/skill/skill-revision-detail.tsx`
- Create: `frontend/src/components/skill/portable-compatibility-panel.tsx`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog-types.ts`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-kind-icon.tsx`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-list.tsx`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-current-column.tsx`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-catalog-panel.tsx`
- Create: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-resource-panels.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-detail-tabs.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-detail-dialog.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-evaluation-tab.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-evaluation-summary-badge.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-quality-badges.test.tsx`
- Create: `frontend/src/components/skill/__tests__/skill-history-tab.test.tsx`
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`

- [x] Extend `Skill` with `latest_evaluation_summary`.
- [x] Extend `Skill` with `health`.
- [x] Add `SkillEvaluationSummaryBadge` states: missing, running, failed, partial, passed, stale.
- [x] Add `SkillHealthBadge` states: ready, needs_evaluation, needs_rerun, needs_credentials, evaluation_running, evaluation_failed, low_confidence.
- [x] Add evaluation badge rendering to `/skills` cards without changing existing publish/manage actions.
- [x] Add `/skills` state filter chips for needs credentials, needs rerun, evaluation failed, published, and local/draft states without adding new app-level navigation entries.
- [x] Add compact evaluation subtitles to `ToolsSkillsDialog` skill rows.
- [x] Add `SkillDetailTab = 'content' | 'credentials' | 'evaluation' | 'history' | 'metadata'`.
- [x] Add `initialTab?: SkillDetailTab` to `SkillDetailDialog`.
- [x] Support `/skills?detailId=<id>&tab=<tab>` deep links for detail tabs while keeping the route inside `/skills`.
- [x] Add `getVisibleSkillDetailTabs(skill, state, initialTab)` and cover the conditional tab rules from the Frontend UX section.
- [x] Add `대화로 개선` / `Improve by chat` action in `SkillDetailDialog`.
- [x] Start a `mode="improve"` skill builder session from that action.
- [x] Split the current text editor, package editor, file preview, package sidebar, package footer, and credential binding panel out of `skill-detail-dialog.tsx`.
- [x] Refactor `skill-detail-dialog.tsx` so it renders one `DialogShell.Body` and one `DialogShell.Footer`.
- [x] Keep package file editing behavior intact in the `Files` content tab.
- [x] Move credential bindings from the content editor surface into the `Credentials` tab.
- [x] Do not render a blank Credentials tab when a skill has no credential requirements and no credential-related deep link.
- [x] In the `Credentials` tab, show required/optional badges, definition key, current binding state, and a missing-required summary at the top.
- [x] When `health.state === "needs_credentials"`, render the skill card/detail status as `자격증명 필요` / `Needs credentials`.
- [x] In the `Evaluation` tab, if required credentials are missing, replace `평가 실행` with `자격증명 연결` and switch to the `Credentials` tab on click.
- [x] After binding or deleting a credential, invalidate skill detail, skill list, marketplace item, and evaluation queries so Skill Health updates immediately.
- [x] Add `Metadata` tab using existing `useUpdateSkillMetadata`.
- [x] Add an `Evaluation` tab/surface to the existing skill detail dialog when visible tab rules require it.
- [x] Add a `History` tab/surface to the existing skill detail dialog when visible tab rules require it.
- [x] Show the legacy empty-state copy when History is opened for a skill with no revisions.
- [x] In `History`, list revisions newest first with operation, revision number, changelog summary, current marker, content hash, and file count.
- [x] In `History`, show selected revision detail with changelog items, changed files, compatibility result, and evaluation snapshot.
- [x] Disable rollback for the current revision.
- [x] Add rollback confirmation copy: `이전 버전으로 되돌리면 현재 내용은 새 이력으로 보존됩니다.`
- [x] On rollback success, refresh skill data, files/content, evaluation summaries, and revision list.
- [x] Render `PortableCompatibilityPanel` in builder preview and revision detail.
- [x] Show latest evaluation summary, stale badge, reusable evaluation sets, run history, and selected run details.
- [x] Show estimate confirmation before creating an evaluation run.
- [x] Add a rerun action that creates a new `SkillEvaluationRun` for the selected evaluation set.
- [x] Add a cancel action for queued/running/grading runs.
- [x] Show improvement conflict state when the backend returns 409 for a changed base hash.
- [x] Add component tests for health badge states, visible tab rules, credential-missing evaluation block, credentials tab focus, evaluation badge states, empty evaluation tab state, latest summary, estimate confirmation, stale badge, rerun callback, cancel callback, history empty state, history list, rollback confirmation, and compatibility panel states.
- [x] Add focused `SkillBuilderPreview` tests for improve file diffs, grouped validation, changelog, and benchmark rendering.
- [x] Add focused `PortableCompatibilityPanel` tests for target labels, status badges, issue details, and empty state.
- [x] Add focused `SkillEvaluationTab` tests for rerun, active-run cancel, missing-credential connect callbacks, stale badges, run history, and selected run detail.
- [x] Add focused `SkillHistoryTab` tests for newest-first ordering, current marker, operation labels, legacy empty state, selected revision details, current rollback disablement, and rollback confirmation.
- [x] Add focused `SkillCredentialBindingsPanel` tests for missing-required summary and connected/missing binding states.
- [x] Add focused `getVisibleSkillDetailTabs` tests for hidden optional tabs, credential requirements, evaluation signal, history signal, and deep links.
- [x] Add focused `skill-state-filters` tests for stale rerun, publication/local state, and combined filters.
- [x] Add focused `ToolsSkillsDialog` skill picker tests for compact skill quality badges without rerun/cancel controls.
- [x] Add package detail/API regression coverage for the `.skill` export action, default export URL, and optional eval artifact inclusion.
- [x] Add mock-only Playwright coverage for package detail `.skill` export download in `frontend/e2e/skill-export.spec.ts`.
- [x] Add mock-only Playwright coverage for installed skill evaluation rerun/cancel controls in `frontend/e2e/skill-evaluation-actions.spec.ts`.
- [x] Add mock-only Playwright coverage for installed skill history rendering in `frontend/e2e/skill-history.spec.ts`.
- [x] Add mock-only Playwright coverage for `/skills` state filter chips in `frontend/e2e/skill-state-filters.spec.ts`.
- [x] Add mock-only Playwright coverage for Skill Builder improve preview rendering in `frontend/e2e/skill-builder-preview.spec.ts`.
- [x] Add mock-only Playwright coverage for Skill Builder improve conflict recovery in `frontend/e2e/skill-builder-conflict.spec.ts`.
- [x] Add mock-only Playwright coverage for `/skills` create CTA -> `SkillCreateDialog` -> Skill Builder chat -> confirm handoff in `frontend/e2e/skill-builder-create.spec.ts`.
- [x] Add detail-dialog regression tests for package file selection, file save/delete behavior, text skill save behavior, and footer actions after the single-shell refactor.
- [x] Add a regression test or routing assertion that no separate user-facing skill evaluation/history/credential route is introduced for this phase.
- [x] Add i18n messages in both Korean and English for the installed skill detail tabs and evaluation actions.
- [x] Run:

```bash
cd frontend
pnpm lint:i18n
pnpm lint:design-system
```

Expected: both pass. Fix any new copy or design-system violations.

### Task 13: Backend Full Verification

**Files:**

- All backend files touched by Tasks 1-9.

- [x] Run:

```bash
cd backend
uv run ruff check .
```

Expected: no new lint errors.

- [x] Run:

```bash
cd backend
uv run pytest tests/test_skills.py tests/test_skill_export.py tests/test_skills_api_regression.py tests/test_skill_bindings.py -q
```

Expected: existing skill behavior still passes.

- [x] Run:

```bash
cd backend
uv run pytest tests/test_skill_builder_service.py tests/test_skill_builder_validator.py tests/test_skill_builder_api.py tests/test_skill_builder_package_builder.py tests/test_skill_package_hash.py tests/test_skill_compatibility.py tests/test_skill_revision_service.py tests/test_skill_revision_backfill.py tests/test_skill_revisions_api.py tests/test_skill_evaluation_service.py tests/test_skill_evaluation_worker.py tests/test_skill_evaluations_api.py tests/test_skill_builder_audit.py tests/test_skill_evaluation_audit.py tests/test_skill_executor_credential_audit.py -q
```

Expected: all new builder tests pass.

### Task 14: Frontend Verification

**Files:**

- All frontend files touched by Tasks 10-12.

- [x] Run:

```bash
cd frontend
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
```

Expected: no new lint/design/i18n errors.

- [x] Run targeted frontend checks for the installed skill evaluation action slice:

```bash
cd frontend
pnpm exec vitest run src/components/skill/__tests__/skill-evaluation-tab.test.tsx src/components/skill/__tests__/skill-quality-badges.test.tsx tests/pages/skills.test.tsx
pnpm exec eslint src/components/skill/skill-evaluation-tab.tsx src/components/skill/skill-evaluation-estimate-dialog.tsx src/components/skill/__tests__/skill-evaluation-tab.test.tsx
pnpm exec eslint --no-ignore e2e/skill-evaluation-actions.spec.ts
pnpm exec tsc --noEmit --pretty false
PW_SKIP_BACKEND=1 E2E_FRONTEND_PORT=3112 E2E_BACKEND_PORT=8112 E2E_WORKERS=1 pnpm exec playwright test e2e/skill-evaluation-actions.spec.ts --workers=1
pnpm exec vitest run src/components/skill/__tests__/skill-history-tab.test.tsx src/components/skill/__tests__/skill-evaluation-tab.test.tsx src/components/skill/__tests__/skill-quality-badges.test.tsx tests/pages/skills.test.tsx
pnpm exec eslint src/components/skill/skill-history-tab.tsx src/components/skill/__tests__/skill-history-tab.test.tsx
pnpm exec eslint --no-ignore e2e/skill-history.spec.ts
PW_SKIP_BACKEND=1 E2E_FRONTEND_PORT=3113 E2E_BACKEND_PORT=8113 E2E_WORKERS=1 pnpm exec playwright test e2e/skill-history.spec.ts --workers=1
pnpm exec vitest run tests/components/agent/tools-skills-dialog-quality.test.tsx
pnpm exec eslint tests/components/agent/tools-skills-dialog-quality.test.tsx
pnpm exec vitest run src/components/skill/__tests__/skill-detail-dialog.test.tsx src/components/skill/__tests__/skill-detail-package-footer.test.tsx src/lib/api/__tests__/skills-api.test.ts
pnpm exec eslint src/lib/api/skills.ts src/lib/api/__tests__/skills-api.test.ts src/components/skill/skill-detail-package-editor.tsx src/components/skill/skill-detail-package-footer.tsx src/components/skill/__tests__/skill-detail-dialog.test.tsx src/components/skill/__tests__/skill-detail-package-footer.test.tsx
pnpm exec eslint --no-ignore e2e/skill-export.spec.ts
PW_SKIP_BACKEND=1 E2E_FRONTEND_PORT=3115 E2E_BACKEND_PORT=8115 E2E_WORKERS=1 pnpm exec playwright test e2e/skill-export.spec.ts --workers=1
pnpm exec eslint --no-ignore e2e/skill-builder-create.spec.ts
PW_SKIP_BACKEND=1 E2E_FRONTEND_PORT=3114 E2E_BACKEND_PORT=8114 E2E_WORKERS=1 pnpm exec playwright test e2e/skill-builder-create.spec.ts --workers=1
```

Expected: all targeted frontend checks pass; screenshot evidence is saved under `output/e2e-captures/20260615-skill-eval-actions/`, `output/e2e-captures/20260615-skill-history/`, `output/e2e-captures/20260615-skill-builder/`, and `output/e2e-captures/20260615-skill-export/`.

- [x] Run:

```bash
cd frontend
pnpm build
```

Expected: Next.js build completes.

### Task 15: Manual End-To-End Check

Use worktree port rules from `AGENTS.md`.

- [x] Start backend:

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8001 --reload-dir app
```

- [x] Start frontend:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev --port 3000
```

- [x] Leave `NEXT_PUBLIC_CHAT_RUNTIME` unset for the primary pass so the app shell uses the current default `langgraph_v3` chat runtime.
- [x] In browser, log in as a dev user.
- [x] Verify the app-level navigation still has one `Skills` entry and no separate skill evaluation, skill history, skill credentials, rollback, or compatibility menu item.
- [x] Open `/skills`.
- [x] Verify the primary page CTA is "대화로 만들기".
- [x] Verify `/skills` uses kind tabs plus state filter chips for credential/evaluation/publish states instead of sending the user to separate skill management pages.
- [x] Browser smoke evidence saved to `output/e2e-captures/20260615-skill-builder-manual/skills-page.png` after login with the seeded E2E user. The smoke verified one app-level `Skills` nav entry, the `/skills` URL, the "대화로 만들기" CTA, kind tabs, and state filter chips. Full LLM-backed create/improve/eval flow remains covered by the unchecked steps below.
- [x] Temporarily test a missing `text_primary` System LLM configuration in a safe local DB or mocked API response and verify the conversational builder shows the readiness state instead of a generic 500/toast, while Text and Package Upload still work.
- [x] Mock-only Playwright coverage verifies the empty `/skills` CTA opens Skill Builder creation, keeps the URL on `/skills`, sends requests only to `/api/skill-builder`, shows `SKILL.md`, `agents/openai.yaml`, OpenAI/Codex, Claude Code, and Vercel Agent Skills compatibility status, confirms the draft, refreshes the list, opens the created skill detail dialog, and captures `output/e2e-captures/20260615-skill-builder/builder-create-flow.png`.
- [x] Real browser pass saved `output/e2e-captures/20260615-skill-builder-manual/builder-created-detail-after-fix.actual.png` after creating a Korean meeting-notes package skill through live `/api/skill-builder`. This pass also caught and fixed two live-only regressions: session-level compatibility results were not rendered in the builder preview, and Korean drafts collided on the fallback `skill` slug, leaving confirm sessions stuck in `confirming`.
- [x] Click "대화로 만들기".
- [x] Verify opening the Skill Builder does not navigate into a normal conversation thread, create a normal conversation run, or require the normal chat `ChatRuntimeSection`.
- [x] Ask for a concrete skill, for example:

```text
회의록을 넣으면 액션 아이템, 담당자, 마감일을 표로 정리하는 스킬을 만들어줘.
가능하면 한국어 회의록 기준으로 동작하게 해줘.
```

- [x] Verify the builder asks at most two clarifying questions.
- [x] Verify preview includes `SKILL.md` and `agents/openai.yaml`.
- [x] Run validation.
- [x] Verify validation shows portable compatibility status for OpenAI/Codex, Claude Code, and Vercel Agent Skills.
- [x] Confirm.
- [x] Verify the new skill appears in `/skills`.
- [x] Verify the skill card shows an evaluation badge when an eval was generated, or "평가 없음" when no eval exists.
- [x] Open the skill detail dialog.
- [x] Verify the detail dialog has `Files` or `Content` plus only the relevant advanced tabs for that skill state.
- [ ] For a simple skill with no credentials/evals/revisions, verify blank Credentials/Evaluation/History tabs are not forced into the default tab list.
- [ ] Verify file tree editing still works in the package `Files` tab.
- [ ] Verify credential binding panel renders in the `Credentials` tab.
- [ ] Click `대화로 개선`.
- [ ] Ask the builder to improve one concrete behavior of the existing skill.
- [ ] Verify the builder opens in improve mode and shows original vs proposed file changes.
- [ ] Verify the improve preview shows a generated changelog and compatibility result before apply.
- [ ] Apply the improvement.
- [ ] Verify the existing skill row is updated, not duplicated.
- [ ] Open the `History` tab and verify a new `builder_improvement` revision appears with the changelog summary.
- [ ] Select the previous revision, click `이전 버전으로 되돌리기`, confirm, and verify a new `rollback` revision is created.
- [ ] Verify rollback restores the previous `SKILL.md`/files while preserving the improvement revision in history.
- [ ] Start another improve session, edit the same skill manually before applying, and verify apply returns a conflict state instead of overwriting.
- [ ] Open the `Evaluation` tab.
- [ ] Verify the builder-created evaluation set appears when the builder generated evals.
- [ ] Click `평가 다시 실행` and verify the estimate confirmation appears before the run starts.
- [ ] Start the run and verify a new run appears in history.
- [ ] If the run stays queued/running long enough, click `평가 취소` and verify the run becomes `cancelled`.
- [ ] Edit the skill content and verify previous runs show a stale indicator when their content hash differs.
- [ ] Edit a package skill file under `scripts/` or `references/` and verify `content_hash` changes, previous runs become stale, and a new revision is created.
- [ ] Verify the skill card changes to `재평가 필요` or the matching Skill Health state after content changes.
- [ ] Create or import a skill draft that declares a required `credential_requirements` entry.
- [ ] Verify the skill card and detail header show `자격증명 필요`.
- [ ] Open the `Evaluation` tab for that skill and verify the primary action is `자격증명 연결`, not `평가 실행`.
- [ ] Click `자격증명 연결` and verify the dialog switches to the `Credentials` tab.
- [ ] Bind a matching user credential and verify the health state no longer says `자격증명 필요`.
- [ ] Run the evaluation and verify it records a new run without exposing credential values in evidence, stdout/stderr summaries, or UI metadata.
- [ ] Open `/settings/audit` and verify builder/evaluation lifecycle events are visible with sanitized metadata.
- [ ] Open the bound credential detail dialog and verify a runtime credential audit entry appears when `execute_in_skill` injected the credential.
- [ ] Open an agent settings screen and add skills through `ToolsSkillsDialog`.
- [ ] Verify skill rows in that dialog show compact evaluation status but no rerun controls.
- [ ] Optional rollback smoke: restart the frontend with `NEXT_PUBLIC_CHAT_RUNTIME=legacy` and verify the Skill Builder still opens, because its stream path is builder-specific rather than tied to either normal chat runtime.

## Rollout Strategy

Ship in three increments:

1. **MVP:** create/improve sessions, System LLM readiness handling, conversational draft, validation, compatibility check, credential metadata normalization, changelog preview, confirm/apply, package content-hash invariant, revision snapshots, rollback, baseline backfill/empty state, internal eval template inference.
2. **Quality Loop:** eval generation, durable installed skill evaluation sets/runs, bounded worker/queue, with-skill vs baseline run, grader, benchmark UI, estimate/cancel, Skill Health.
3. **Trigger Optimization:** should-trigger / should-not-trigger eval set, description rewrite, held-out score report.

The MVP is useful without evals, but its data model and UI should already have fields for eval results so Phase 2 does not require a schema rewrite. Do not ship Phase 2 evaluation runs without the worker/concurrency/cancellation policy; otherwise the feature can starve normal chat runtime.

## Risk Register

- **Risk:** Builder-generated files include secrets.
  - Mitigation: run existing `scan_package()` before finalization and block error-level findings.
- **Risk:** Builder creates Moldy-only skills that cannot be reused elsewhere.
  - Mitigation: keep `SKILL.md` frontmatter minimal and put Moldy metadata in `agents/moldy.yaml`.
- **Risk:** Skill descriptions under-trigger.
  - Mitigation: add trigger optimization phase with positive and near-miss negative queries.
- **Risk:** Eval runner becomes expensive.
  - Mitigation: default to 2-3 eval prompts and one run per configuration in the UI; allow more runs only through an explicit advanced action.
- **Risk:** User thinks eval metrics are required to create a skill.
  - Mitigation: keep evals optional and label them as quality checks.
- **Risk:** Normal skill APIs and builder APIs drift.
  - Mitigation: finalize through `skill_service.create_package_skill` and existing skill serialization.
- **Risk:** Future builder confirm, rollback, or package replacement paths bypass package metadata refresh and leave `content_hash` stale.
  - Mitigation: keep package tree hashing centralized in `compute_package_tree_hash`, route package writes through `refresh_package_metadata` or equivalent service helpers, and retain regression tests for upload, `set_skill_file`, and `delete_skill_file`.
- **Risk:** Improve mode overwrites user edits made after the session started.
  - Mitigation: store `base_content_hash` and return 409 conflict when the current hash differs.
- **Risk:** Skill Builder first-run fails with a generic 500 when System LLM `text_primary` is not configured.
  - Mitigation: convert `SystemModelNotConfiguredError` to `SYSTEM_LLM_NOT_CONFIGURED`, show readiness UI, and keep Text/Package Upload paths available.
- **Risk:** The merged LangGraph v3 chat runtime is the default normal chat path, while Skill Builder code accidentally depends on legacy normal-chat SSE internals.
  - Mitigation: keep Skill Builder as a standalone authoring workflow with builder-specific stream helpers, align stream status/activity vocabulary with LangGraph v3, and run tests with `NEXT_PUBLIC_CHAT_RUNTIME` unset.
- **Risk:** Skill Builder reuses normal chat approval/HITL display code without the matching builder-owned redacted-edit restoration path, causing secrets to leak or edited arguments to submit `<redacted>` placeholders.
  - Mitigation: keep builder approvals on `/api/skill-builder/{session_id}/messages/resume`, apply `sensitive-display`-equivalent redaction in the UI, and restore redacted placeholders only from builder session state or persisted draft actions owned by that session.
- **Risk:** Evaluation runs consume shared checkpointer/DB capacity and make normal chat slow or unavailable.
  - Mitigation: use a bounded worker with default concurrency 1, queue limits, no long DB transactions, startup reconciliation, and documented sizing relative to `CHECKPOINTER_POOL_MAX_SIZE`.
- **Risk:** Skill credential injection happens without an auditable credential-use record.
  - Mitigation: write best-effort `credential_service.write_audit_log(action="invoke", source="runtime")` rows from `execute_in_skill` whenever user-bound skill credentials are injected.
- **Risk:** Audit metadata leaks prompts, outputs, command arguments, or secrets.
  - Mitigation: use `audit_service.record_event(...)`, keep metadata to IDs/counts/statuses, and add tests that search audit metadata for known secret/prompt/output strings.
- **Risk:** Audit logs become noisy from read-only skill views.
  - Mitigation: do not audit requirement list reads, binding list reads, tab opens, or skill detail views.
- **Risk:** Evaluation runner bypasses the runtime sandbox because it needs lower-level subprocess control.
  - Mitigation: refactor and reuse `execute_in_skill` command parsing, timeout, runtime root, output dir, and redaction policy.
- **Risk:** Changelog/eval/history text bloats `SKILL.md` and hurts cross-platform skill quality.
  - Mitigation: store changelog and history in `skill_revisions`, compatibility in validation/revision metadata, and keep `SKILL.md` under the validator line limit.
- **Risk:** Rollback overwrites a useful improvement permanently.
  - Mitigation: rollback creates a new revision from an older snapshot and never mutates or deletes prior revision rows.
- **Risk:** Revision snapshots capture secrets or runtime-only outputs.
  - Mitigation: snapshot only portable skill files, run secret scan before snapshot creation, and exclude credential values, eval artifacts, builder messages, and runtime output directories.

## Completion Checklist

- [x] New Skill Builder can create a package skill from chat.
- [ ] Existing skills can be improved through chat and applied back to the same skill row.
- [x] Improve mode shows file diffs and blocks stale-base overwrites with a conflict state.
- [x] Existing text skill creation still works.
- [x] Existing package upload still works.
- [x] Existing package file editor still works on builder-created skills.
- [x] Package skill file update/delete/upload recalculates `content_hash` and marks old evaluations stale by comparison.
- [x] Credential requirement panel shows generated requirements.
- [x] `/skills` primary CTA starts conversational skill creation.
- [x] Skill Builder works in the default app shell, where normal chat uses `langgraph_v3` unless `NEXT_PUBLIC_CHAT_RUNTIME=legacy`.
- [x] Missing System LLM configuration shows a readiness state and does not break text creation or package upload.
- [x] Skill cards show compact evaluation status without losing marketplace publish/manage actions.
- [x] Skill detail dialog has stable `Content` or `Files` editing plus conditionally visible `Credentials`, `Evaluation`, `History`, and `Metadata` surfaces.
- [x] Every skill-changing operation creates an immutable skill revision snapshot.
- [x] Legacy skills with no revision history get a baseline backfill path or a clear history empty-state before first mutation.
- [x] History tab lists revisions and can roll back to an earlier snapshot by creating a new rollback revision.
- [x] Improve mode generates a human-readable changelog before apply and stores it on the resulting revision.
- [x] Changelog, revision history, compatibility reports, and eval reports are not appended to `SKILL.md`.
- [x] Agent settings skill picker shows compact evaluation status without rerun/detail controls.
- [x] Every installed skill can show evaluation sets, latest result, run history, and rerun controls.
- [x] Evaluation templates are selected automatically; users are not forced through a preset picker.
- [x] Evaluation runs store template/version metadata, estimate, runner version, grader prompt version, and eval schema version.
- [x] Evaluation runs are created as durable queued rows and executed through a bounded worker, not through a long HTTP request.
- [x] Evaluation rerun shows a cost/time estimate and supports cancellation or timeout status.
- [x] Skill Health summary appears on cards/detail and reacts to missing evals, stale runs, missing credentials, running evals, failed evals, and low pass rate.
- [x] Builder-time evals are attached to the finalized skill as reusable evaluation records.
- [x] Validation catches missing metadata, weak descriptions, unsafe paths, and secrets.
- [x] Validation catches invalid credential requirements, unknown credential definitions, reversed `env_map`, and undeclared network usage.
- [x] Validation catches portable compatibility issues for OpenAI/Codex, Claude Code, and Vercel Agent Skills targets.
- [x] Missing required skill credentials block evaluation and guide the user to the `Credentials` tab.
- [x] Skill credential binding upsert/delete continues to write existing skill audit events.
- [x] Skill credential injection through `execute_in_skill` writes credential-use audit records without leaking credential values.
- [x] Builder/evaluation audit events appear in `/settings/audit` with sanitized metadata and no prompt/output/file-body leakage.
- [x] Evaluation runner uses the same sandbox, timeout, path, env, and redaction policy as current skill execution.
- [x] Generated package can be exported as `.skill` without `evals/` by default.
- [x] Backend tests pass.
- [x] Frontend lint/build/design/i18n checks pass.
- [ ] Manual `/skills` flow succeeds end to end.
