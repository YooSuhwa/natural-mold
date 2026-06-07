# Moldy PRD

> Last updated: 2026-06-07
> Version: v0.4
> Source basis: current source tree, Alembic head `m59_conversation_artifacts`,
> and recent commits through merge `078250c`.

## Changelog

| Version | Date | Change |
|---------|------|--------|
| v0.4 | 2026-06-07 | Rebased PRD on actual source. Replaced single-user PoC assumptions with ADR-016 multi-user auth, marketplace, memory, Agent API, artifacts, audit, subagents, and runtime split status. |
| v0.3 | 2026-05-26 | Reflected System LLM settings and UI-managed LLM credentials. |
| v0.2 | 2026-05-18 | Reflected marketplace resource design. |
| v0.1 | 2026-04-01 | Initial PoC draft. |

## 1. Product Overview

Moldy is a self-hostable no-code AI agent builder. Users create agents through
guided conversation or manual configuration, attach tools/MCP/skills/credentials,
chat with those agents, schedule automated runs, and share or install reusable
resources through a marketplace.

Current product stage is beyond the original PoC. The codebase supports
multi-user operation, operator-managed system resources, external Agent API
calls, generated file artifacts, audit logs, memory policy controls, subagent
delegation, and marketplace installation/publishing for skills.

## 2. Users and Permissions

| User type | Description | Main actions |
|-----------|-------------|--------------|
| Regular user | Authenticated user with personal resources | Create agents, chat, register credentials, connect tools/MCP/skills, install marketplace resources, configure schedules |
| Creator | User who publishes a skill/resource | Publish versions, choose visibility, manage restricted recipients |
| Installer | User who installs marketplace resources | Bind required credentials, attach installed skills to agents, update installed copies |
| Operator / `super_user` | Admin role from ADR-016 | Manage system credentials, System LLM settings, listed marketplace state, audit/security settings |
| External API caller | Server-side system using Agent API key | Invoke deployed agents through `/v1` blocking/streaming endpoints |

Permission model:

- JWT HS256 access token + refresh token rotation.
- HttpOnly cookies (`moldy_at`, `moldy_rt`, `moldy_csrf`) with CSRF
  double-submit for browser write requests.
- `Authorization: Bearer` is supported for API clients.
- User resources are owner-scoped.
- `is_system=True` resources and System LLM settings are `super_user`-managed.
- Missing vs unauthorized resources should collapse to the same external
  response where enumeration risk exists.

## 3. Current Feature Requirements

### P1: Agent Creation and Management

Users can create agents by conversation, template, or manual setup.

Required behavior:

- Builder collects intent, tools, middleware, prompt, image, and final config.
- Agents have name, description, prompt, model, LLM credential binding,
  fallback model list, opener questions, identity mode, tools, MCP tools,
  skills, middleware, subagents, memory settings, and schedules.
- Agent settings support form mode and visual settings mode.
- First message can create the conversation lazily rather than pre-creating
  empty conversations.

Current source markers:

- `backend/app/agent_runtime/builder_v3/`
- `backend/app/services/agent_service.py`
- `backend/app/services/agent_invocation_service.py`
- `frontend/src/app/agents/`
- commit `6770ba7` for agent settings draft-state extraction

### P1: Chat, Streaming, Branching, and Traces

Users chat with agents through a streaming UI.

Required behavior:

- SSE streams assistant text, tool updates, interrupts, usage, traces, and
  generated artifact metadata.
- Editing a user message or regenerating an assistant turn forks a LangGraph
  branch and updates `active_branch_checkpoint_id`.
- HiTL approvals, `ask_user`, clarifying questions, and option-list UI are
  rendered as structured cards.
- Public share links render read-only conversations without login.
- Trace debugger surfaces tool/model/subagent spans for authenticated users.

Current source markers:

- `backend/app/routers/conversation_messages.py`
- `backend/app/routers/conversation_branches.py`
- `backend/app/agent_runtime/streaming.py`
- `backend/app/models/message_event.py`
- `frontend/src/lib/chat/use-chat-runtime.ts`
- `frontend/src/components/chat/`
- commit `243b5db` for conversation router split

### P1: Credentials and Model Management

Users and operators can manage credentials safely.

Required behavior:

- Credential payloads are encrypted with Cipher V2.
- List APIs use `field_keys` without decrypting full payloads.
- System credentials are separate from user credentials.
- Model discovery can load provider models from selected credentials.
- System LLM settings choose role-specific models for builder, assistant, and
  image generation.
- Runtime LLM key resolution respects direct agent binding, model defaults, and
  allowed fallbacks without leaking other users' keys.

Current source markers:

- `backend/app/credentials/`
- `backend/app/security/cipher.py`
- `backend/app/models/system_llm_setting.py`
- `backend/app/routers/system_llm_settings.py`
- `backend/app/agent_runtime/credential_resolution.py`
- ADR-007, ADR-009, ADR-013, ADR-019

### P1: Tools, MCP, and Built-ins

Agents can call built-in tools, registry tools, and MCP tools.

Required behavior:

- Built-ins include web search/scraper, current datetime, relative-date resolver,
  Tavily search, HTTP request, Naver search, Google CSE, Gmail, Calendar, and
  Google Chat webhook where configured.
- MCP servers support stdio, SSE, and Streamable HTTP.
- MCP registry presets can prefill server settings and probe tools before save.
- MCP health polling refreshes server status.
- First-party MCP presets can receive per-user `mcp_secret` through the
  `X-Moldy-Credential` header.

Current source markers:

- `backend/app/tools/registry.py`
- `backend/app/agent_runtime/tool_factory.py`
- `backend/app/mcp/client.py`
- `backend/app/mcp/discovery.py`
- `backend/app/services/mcp_registry.py`
- `backend/app/data/mcp_server_registry.json`

### P1: Skills and Marketplace

Users can create, upload, install, publish, and attach skills.

Required behavior:

- Skills support `text` and `.skill` package forms.
- Package extraction rejects zip-slip, symlink, null byte, and oversized payloads.
- Secret scan rejects publish/upload packages with likely secrets.
- Skill runtime exposes selected skills under a per-thread runtime path.
- Skill credential requirements can be bound to user credentials.
- Installed marketplace skills are user-owned copies with origin/publication
  metadata.
- Publish creates immutable marketplace versions; install/update copies version
  snapshots.
- Operators can manage listed state and moderation.

Current source markers:

- `backend/app/models/skill.py`
- `backend/app/skills/`
- `backend/app/marketplace/`
- `backend/app/routers/marketplace.py`
- `frontend/src/app/marketplace/`
- ADR-017, ADR-018

### P1: Schedules and Triggers

Users can run agents automatically.

Required behavior:

- Cron and interval triggers are backed by APScheduler.
- Schedules support timezone, run limits, end time, pause-on-failure, and
  conversation policy.
- Trigger runs record source, output preview, duration, thread/checkpoint/trace
  metadata.
- Trigger mode disables interactive `ask_user`/HiTL flows.

Current source markers:

- `backend/app/models/agent_trigger.py`
- `backend/app/models/agent_trigger_run.py`
- `backend/app/agent_runtime/trigger_executor.py`
- `backend/app/services/trigger_service.py`
- migrations M47-M50

### P1: Memory

Users control long-term memory behavior.

Required behavior:

- User settings control enabled/read/write policy and allowed scopes.
- Agent settings can override user policy.
- Memory records support user-wide and agent-specific scopes.
- Write policy can be `off`, `ask`, or `auto`; trigger write policy can be
  `off` or `auto`.
- Runtime tools save or propose memory according to policy and never persist
  secrets.

Current source markers:

- `backend/app/models/memory.py`
- `backend/app/services/memory_service.py`
- `backend/app/agent_runtime/tools/memory.py`
- `frontend/src/app/settings/memory/page.tsx`
- commit `def260b`

### P1: Generated Artifacts

Agents can generate files that persist outside a single message.

Required behavior:

- Runtime instructs agents to write user-visible files under
  `/conversations/{thread_id}/...`.
- Generated files are indexed as `conversation_artifacts`.
- Artifact versions track storage object, filename, size, hash, and metadata.
- Chat right rail and `/artifacts` library preview common formats.
- Share pages expose allowed artifacts without leaking private credentials.

Current source markers:

- `backend/app/models/conversation_artifact.py`
- `backend/app/services/artifact_service.py`
- `backend/app/routers/artifacts.py`
- `frontend/src/components/chat/artifacts/`
- `frontend/src/components/artifacts/`
- migration M59, commit `83bf67d`

### P1: External Agent API

Operators/users can expose fixed-identity agents to server-side systems.

Required behavior:

- API deployments map public IDs to agents.
- API keys use `moldy_sk_...` secrets and deployment scopes.
- Blocking, streaming, stateful thread, Dify-style, and OpenAI-compatible
  endpoints are available under `/v1`.
- Agent API runs are recorded for observability.
- Browser CSRF is not used for `/v1`; API-key auth is required.

Current source markers:

- `backend/app/agent_api/`
- `backend/app/routers/agent_api.py`
- `backend/app/routers/agent_runtime_api.py`
- `backend/app/models/agent_api.py`
- `docs/agent-api.md`
- migration M56

### P2: Observability, Usage, and Audit

The product should give operators enough runtime visibility to debug and govern
agent usage.

Current behavior:

- Token usage and daily spend rollups exist by user/agent/model.
- Hook framework records spend and runtime events.
- LangSmith and Langfuse integration can correlate external traces.
- Audit events capture access denials and marketplace/admin actions.
- Health checks track models and MCP servers.

Current source markers:

- `backend/app/hooks/`
- `backend/app/observability/langfuse.py`
- `backend/app/services/spend_writer.py`
- `backend/app/models/audit_event.py`
- `backend/app/routers/audit.py`
- migration M57

## 4. Non-Goals

- Anonymous public execution of arbitrary agents.
- Marketplace billing, ratings, or ranking algorithms.
- Organization/team RBAC beyond current `super_user` vs regular user.
- Full sandbox isolation for arbitrary package code beyond the current
  allowlist, timeout, filesystem permission, and redaction controls.
- Tool marketplace for user-defined tools. Tools remain code/registry/system
  resources; marketplace currently focuses on skills and lays schema groundwork
  for MCP/Agent resources.
- Mobile-native app.

## 5. Key Data Model Groups

| Group | Current tables |
|-------|----------------|
| Auth | `users`, `refresh_tokens` |
| Agents | `agents`, `agent_tools`, `agent_skills`, `agent_mcp_tools`, `agent_subagents` |
| Chat | `conversations`, `message_events`, `message_event_chunks`, `message_attachments`, `message_feedback` |
| Artifacts | `conversation_artifacts`, `artifact_versions` |
| Credentials | `credentials`, `credential_defaults`, `credential_audit_logs` |
| Marketplace | `marketplace_items`, `marketplace_versions`, ACL, installation, publication, skill binding tables |
| Memory | `user_memory_settings`, `agent_memory_settings`, `memory_records`, `memory_proposals` |
| Agent API | `agent_deployments`, `agent_api_keys`, `agent_api_threads`, `agent_api_runs` |
| Scheduling | `agent_triggers`, `agent_trigger_runs` |
| Usage/audit | `token_usages`, daily spend rollups, `audit_events`, `health_check_history` |

There is no current standalone `messages` ORM model. Message state is derived
from LangGraph checkpoints and persisted SSE event traces.

## 6. API Families

| Family | Representative endpoints |
|--------|--------------------------|
| Auth | `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, refresh/session endpoints |
| Agents | `/api/agents`, `/api/agents/{id}`, `/api/middlewares` |
| Builder | `/api/builder/*`, conversational creation routes |
| Conversations | `/api/agents/{id}/conversations`, `/api/conversations/{id}/messages`, branch/file/trace endpoints |
| Resources | `/api/tools`, `/api/mcp-*`, `/api/skills`, `/api/models`, `/api/templates` |
| Credentials | `/api/credentials`, system credential endpoints, model discovery/test endpoints |
| Marketplace | `/api/marketplace/items`, install/update/uninstall, publish, versions, ACL, admin listed state |
| Memory | `/api/memory/*`, agent memory settings |
| Artifacts | `/api/artifacts/*`, conversation file endpoints |
| Schedules | `/api/agents/{id}/triggers`, run history endpoints |
| Shares | `/api/shares/*`, `/shared/{shareId}` frontend route |
| Agent API | `/v1/runs/wait`, `/v1/runs/stream`, `/v1/threads`, compatibility endpoints |

## 7. Success Criteria

Functional:

- A regular user can create an agent, attach at least one tool and one skill,
  chat, branch a response, and see persisted stream events.
- A user can install a marketplace skill, bind required credentials, attach it
  to an agent, and run it without exposing unselected skills.
- A generated file appears both in chat right rail and artifact library.
- A schedule can run an agent and record run metadata.
- An API key can invoke a fixed-identity deployment through `/v1/runs/wait`.

Security and governance:

- Regular users cannot read other users' resources or system credentials.
- CSRF is required for browser write endpoints.
- Credential values are never returned in list/detail payloads.
- Marketplace secret scan blocks likely secrets before publish.
- Audit records access-denied and admin/marketplace actions.

Verification:

- Backend: `cd backend && uv run ruff check . && uv run pytest`
- Frontend: `cd frontend && pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm build`
- E2E where UI behavior changed: `cd frontend && pnpm test:e2e`

## 8. Recent Implementation Notes

| Date | Commit | Product impact |
|------|--------|----------------|
| 2026-06-07 | `e2178d6` | Runtime executor split makes the runtime easier to maintain without changing public behavior. |
| 2026-06-07 | `243b5db` | Conversation API responsibilities split for CRUD/messages/branches/files/traces. |
| 2026-06-07 | `ca54bdc` | Chat no longer creates empty conversations before the first user message. |
| 2026-06-07 | `12c8b98` | Heavy markdown/artifact preview modules lazy-load to improve chat performance. |
| 2026-06-06 | `83bf67d` | Generated file previews and artifact library shipped with M59. |
| 2026-06-05 | `05e6ea6` | Subagent delegation wired into runtime identity and chat. |
| 2026-06-05 | `def260b` | Long-term memory controls and runtime memory tools shipped. |

## 9. Terms

| Term | Meaning |
|------|---------|
| Agent | Configured AI worker with prompt, model, tools, skills, middleware, and identity |
| Skill | Reusable `SKILL.md` package or text instruction bundle mounted at runtime |
| MCP | Model Context Protocol server and tools exposed to agents |
| System resource | Operator-managed resource, generally visible or attachable but not editable by regular users |
| Marketplace item | Logical shared resource with visibility, owner/system state, and versions |
| Marketplace version | Immutable installable snapshot of an item |
| Artifact | Generated file captured from a chat run and available for preview/download |
| Memory | User-wide or agent-specific long-term record governed by policy |
| Agent API | External `/v1` API for server-side invocation of deployed agents |
