# Moldy Architecture Map

> Last updated: 2026-06-13
> Source basis: current working tree on `codex/fix-frontend-docker-lock-docs`,
> recent runtime commits, and the files under `backend/app/`,
> `frontend/src/`, and `frontend/e2e/`.

Moldy is a multi-user no-code AI agent builder. The current codebase is no
longer an M1/M2 migration plan: it runs on `deepagents.create_deep_agent`, uses
LangGraph `AsyncPostgresSaver`, has JWT + HttpOnly cookie auth, and includes
marketplace, memory, Agent API, audit, generated artifacts, subagents, and
schedule productization.

## Current Snapshot

| Area | Current source state |
|------|----------------------|
| Backend | FastAPI app factory in `backend/app/main.py`, async SQLAlchemy services, 49 ORM tables |
| Frontend | Next.js 16.2.2 + React 19.2.4 App Router, `next-intl`, TanStack Query, Jotai |
| Runtime | LangChain 1.x + LangGraph 1.x + `deepagents>=0.6.8,<0.7.0` |
| Database | PostgreSQL 16, Alembic head `m59_conversation_artifacts` |
| Auth | ADR-016 JWT HS256, HttpOnly cookies, CSRF double-submit, refresh rotation, `super_user` |
| Credentials | Cipher V2, system/user split, 22 credential definitions registered |
| Marketplace | Catalog, install, update, uninstall, publish, ACL, moderation/listing, k-skill importer |
| Latest major feature | M59 generated conversation artifacts + artifact preview/library |

## System Overview

```
Frontend (Next.js App Router)
  app routes -> components -> lib/api + lib/hooks + lib/stores
       | HTTP, fetch-event-source SSE
       v
Backend (FastAPI)
  routers -> services -> models/schemas
       |              |
       |              +-> SQLAlchemy async session -> PostgreSQL 16
       |
       +-> agent_runtime
             runtime_config -> runtime_component_builder -> create_deep_agent
             agent_stream_runner -> streaming -> SSE + message_events
             langgraph_agent_stream_runner -> Agent Streaming Protocol BFF
             checkpointer -> LangGraph AsyncPostgresSaver
```

## Backend Composition

### App Startup

`backend/app/main.py` owns process-level setup:

- production safety checks for secrets, cookies, CORS, and first-user-admin
- default model/template seeding
- local E2E user seeding outside production
- default marketplace skill seeding
- system credential bootstrap from environment in non-production
- LangGraph checkpointer initialization
- hook registration and spend writer startup
- scheduler leadership via Postgres advisory lock
- recurring jobs for credential rotation, model/MCP health, model catalog updates,
  EventBroker GC, refresh-token GC, and skill runtime cleanup

### Router Surface

`backend/app/routers/` is split by resource and workflow:

| Router group | Purpose |
|--------------|---------|
| `auth`, `credentials`, `system_llm_settings` | login/session, user/system credentials, operator model slots |
| `agents`, `builder`, `assistant` | agent CRUD, conversational builder, assistant panel |
| `conversations` facade | includes `conversation_crud`, `conversation_messages`, `conversation_branches`, `conversation_files`, `conversation_traces` |
| `tools`, `mcp`, `skills`, `models`, `templates` | resource catalogs and settings |
| `marketplace` | item list/detail, install/update/uninstall, publish/version/ACL/admin listing |
| `memory`, `artifacts`, `shares`, `feedback` | memory controls, generated file library, public shares, feedback |
| `triggers`, `usage`, `audit`, `health`, `uploads` | schedules, spend/usage, audit log, health and uploads |
| `agent_api`, `agent_runtime_api` | external `/v1` Agent API and runtime compatibility endpoints |

### Service Layer

The backend keeps the Router -> Service -> Model direction:

- `agent_invocation_service.py` builds `AgentConfig` for chat, trigger, and API
  sources, including identity resolution, credential subject, fallback chain,
  tool config, skill descriptors, and subagent config.
- `conversation_stream_service.py`, `conversation_branch_service.py`,
  `conversation_file_service.py`, and `conversation_audit_service.py` hold the
  post-`243b5db` conversation responsibilities that used to sit in one router.
- `marketplace/*_service.py` handles resource visibility, installation,
  publish/version snapshots, credential requirements, redaction, and k-skill import.
- `memory_service.py` resolves user/agent memory policies and stores records or
  approval proposals.
- `artifact_service.py` and `artifact_storage.py` persist generated file metadata
  and local artifact bytes introduced by M59.

## Agent Runtime

`backend/app/agent_runtime/executor.py` is now a compatibility facade. Commit
`e2178d6` split the implementation into focused modules:

| Module | Responsibility |
|--------|----------------|
| `runtime_config.py` | `AgentConfig`, `RuntimeComponents`, data root |
| `runtime_component_builder.py` | model candidates, fallback/retry middleware, tools, skills, memory, permissions, subagents |
| `agent_stream_runner.py` | streaming/invoke execution, hooks, Langfuse context |
| `langgraph_agent_stream_runner.py` | LangGraph v3 protocol execution and resume runs |
| `mcp_tool_loader.py` | MCP runtime tool loading and error stubs |
| `skill_executor.py` | `execute_in_skill`, shell var expansion, subprocess timeout |
| `streaming.py` | LangGraph events -> internal SSE events, usage, traces, artifacts |
| `langgraph_streaming.py` | LangGraph v3 events -> Agent Streaming Protocol events, broker, trace persistence, side effects |
| `checkpointer.py` | global `AsyncPostgresSaver` lifecycle |
| `model_factory.py` | provider-specific chat model construction and quirks |
| `middleware_registry.py` | 22 middleware catalog and explicit/provider middleware split |
| `subagents.py` | parent/child agent delegation config |

### Chat Flow

Legacy SSE path:

```
POST /api/conversations/{id}/messages
  -> conversation_messages router validates ownership + CSRF
  -> agent_invocation_service.build_agent_config_for_conversation()
  -> agent_stream_runner.execute_agent_stream()
  -> runtime_component_builder._prepare_agent()
  -> create_deep_agent(...)
  -> streaming.stream_agent_response()
  -> SSE to frontend + message_events/message_event_chunks persistence
```

Feature-flagged LangGraph v3 path:

```
Frontend @langchain/react useStream
  -> POST /api/conversations/{cid}/langgraph/threads/{tid}/commands
  -> conversation_agent_protocol validates auth/ownership + CSRF
  -> run.start or input.respond creates ConversationRun
  -> conversation_run_worker executes langgraph_agent_stream_runner
  -> langgraph_streaming emits Agent Streaming Protocol events
  -> EventBroker live stream + message_events protocol trace persistence
  -> /stream/events returns SDK-compatible SSE subscriptions
```

The v3 stream routes have two attach modes. Content channels attach to the
active run broker or replay stored protocol events. Lifecycle/input subscriptions
use a thread-level stream that survives HITL resume and broker replacement,
replays persisted events, and throttles idle DB replay polling.

Runtime details:

- model fallback chain is resolved before the runtime, then applied through
  `ModelFallbackMiddleware` and retry middleware;
- temporal grounding tools are always appended (`current_datetime`,
  `resolve_relative_date`);
- skill-declared tool dependencies are auto-injected;
- memory tools are added only when the effective memory write policy allows it;
- trigger mode uses non-streaming invoke and disables `ask_user`/HiTL interrupts;
- chat and Agent API sources carry identity metadata for credential resolution
  and audit/hooks.

### Skills and Filesystem

The current skill runtime is selected-skill based, not a broad `/skills/` mount:

- `build_skill_runtime_context()` creates per-thread runtime skill exposure under
  `/runtime/{thread_id}/.../skills/`.
- `resolve_runtime_credentials()` maps skill credential requirements to runtime
  environment variables.
- `build_filesystem_permissions()` constrains Deep Agents filesystem access by
  thread, agent, user, selected skill slugs, and subagent runtime name.
- `execute_in_skill` remains Python-script allowlisted with timeout and output
  directory controls.
- generated user-visible files must be written under
  `/conversations/{thread_id}/...` and are recorded as conversation artifacts.

## Data Model Groups

Alembic head is `m59_conversation_artifacts`. The ORM currently exposes 49
tables across these groups:

| Group | Tables / models |
|-------|-----------------|
| Identity/auth | `users`, `refresh_tokens` |
| Agents | `agents`, `agent_tools`, `agent_skills`, `agent_mcp_tools`, `agent_subagents`, `models`, `templates` |
| Runtime/chat | `conversations`, LangGraph checkpoint tables, `message_events`, `message_event_chunks`, `message_attachments`, `message_feedback`, `conversation_artifacts`, `artifact_versions` |
| External API | `agent_deployments`, `agent_api_keys`, `agent_api_key_deployments`, `agent_api_threads`, `agent_api_runs` |
| Scheduling | `agent_triggers`, `agent_trigger_runs` |
| Resources | `tools`, `skills`, `mcp_servers`, `mcp_tools`, marketplace item/version/ACL/installation/publication/binding tables |
| Credentials | `credentials`, `credential_defaults`, `credential_audit_logs` |
| Memory | `user_memory_settings`, `agent_memory_settings`, `memory_records`, `memory_proposals` |
| Observability/usage | `token_usages`, daily spend rollups, `health_check_history`, `audit_events` |
| Settings | `system_llm_settings` |

There is no standalone `messages` ORM model in the current source. Conversation
message state is reconstructed from LangGraph checkpoints and persisted event
traces; `message_events` and chunk rows support streaming resume, traces, share
rendering, and artifact linkage.

## Marketplace

ADR-017 Phase 1 is implemented beyond the original planning baseline:

- marketplace tables landed in M40, skill lineage in M41, `agent_skills.config`
  in M42, skill credential bindings in M43, relative storage in M44;
- catalog list/detail/version APIs are implemented;
- install/update/uninstall flows copy immutable version snapshots into the
  user's installed skills;
- publish flows snapshot user skills, perform secret scan, manage ACL, and
  create immutable versions;
- super_user admin actions can toggle listing and disable items;
- k-skill import and requirement mapping exist in `backend/app/marketplace/`;
- frontend marketplace routes include catalog, item detail, and admin moderation.

Tool marketplace remains out of scope. Runtime tools are still seeded or defined
through code/registry and attached to agents, MCP servers, or skills.

## Frontend Map

`frontend/src/app/` includes product surfaces for:

- auth: `/login`, `/register`
- agents: list/detail, chat, settings, visual settings, manual/template/conversational creation
- chat traces: `/agents/[agentId]/conversations/[conversationId]/traces`
- resources: `/tools`, `/skills`, `/mcp-servers`, `/credentials`, `/models`
- marketplace: `/marketplace`, detail pages, `/marketplace/admin/moderation`
- settings: system credentials, system LLM, Agent API, memory, schedules, audit,
  security, usage, artifacts, appearance
- shared conversations: `/shared/[shareId]`
- artifact library: `/artifacts`

State flow:

```
lib/api/* -> lib/hooks/* (TanStack Query) -> components/app routes
lib/stores/* (Jotai) for chat rail, artifacts, sidebar, local UI state
lib/sse/* and lib/chat/use-chat-runtime.ts for legacy streaming and resume
lib/chat/langgraph-runtime/* for the feature-flagged LangGraph v3 runtime
```

The LangGraph v3 frontend path is selected with
`NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3`. `useMoldyLangGraphStream` owns a single
`@langchain/react` stream per conversation/thread, bridges root messages into
assistant-ui with `useExternalStoreRuntime`, keeps the raw stream available for
DeepAgents selectors, and routes HITL resume through `stream.respond`.

Recent frontend refactors:

- `6770ba7` extracted agent settings draft state to
  `use-agent-settings-draft.ts` and `agent-settings-draft.ts`;
- `12c8b98` split heavy markdown/artifact previews behind lazy boundaries;
- static UI copy is expected to live in `frontend/messages/ko.json` and
  `frontend/messages/en.json`.

## Agent API

External callers use `/v1` endpoints with `moldy_sk_...` API keys:

- deployments are managed through `/api/agent-api/*`;
- runtime calls include `/v1/runs/wait`, `/v1/runs/stream`, `/v1/threads`,
  Dify-style compatibility endpoints, and OpenAI-compatible chat completions;
- API deployments require fixed agent identity;
- API keys are user-owned and scoped to deployments.

See `docs/agent-api.md` for request examples.

## Recent Source-Aligned Changes

| Date | Commit | Change reflected in docs |
|------|--------|--------------------------|
| 2026-06-13 | pending | Add LangGraph v3 Agent Streaming Protocol BFF path, assistant-ui bridge, and deterministic v3 E2E |
| 2026-06-07 | `e2178d6` | Split executor into facade + runtime component builder + stream runner + MCP/skill modules |
| 2026-06-07 | `243b5db` | Split conversation router responsibilities into CRUD/messages/branches/files/traces |
| 2026-06-07 | `6770ba7` | Extract frontend agent settings draft hook/lib |
| 2026-06-07 | `ca54bdc` | Defer conversation creation until first message |
| 2026-06-07 | `12c8b98` | Lazy-load heavy chat preview modules |
| 2026-06-06 | `83bf67d` | Add generated file artifacts, M59, artifact preview/library |
| 2026-06-05 | `05e6ea6` | Wire subagent chat delegation and runtime identity |
| 2026-06-05 | `def260b` | Add long-term memory controls, memory tools, settings UI |
| 2026-06-05 | `d5fe960`, `08f5371` | Document E2E capture workflow and GitHub connector PR fallback |

## Open Technical Risks

- Long-running concurrent worktrees can double-run scheduler jobs if multiple
  backend processes share the same DB and all acquire work over time.
- Artifact and preview surfaces are new as of M59 and should keep getting E2E
  coverage around branch links, shares, and generated-file permissions.
- Marketplace supports Skill Phase 1 deeply; MCP/Agent resource publishing is
  still a future expansion even though the schema is resource-type generic.
- `CHECKPOINT.md` and older execution notes are historical records, not current
  architecture references.
