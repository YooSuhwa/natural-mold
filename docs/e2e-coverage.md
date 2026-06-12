# E2E Coverage Matrix

Living record of Playwright E2E coverage across Moldy's feature surface.
Update this whenever you add/change a spec or ship a user-facing feature.

> Goal: every feature that *can* be tested end-to-end has a real E2E test.
> Mocks (`page.route`) are used only where determinism requires it (e.g. token
> usage numbers, third-party error paths); real third-party OAuth stays manual.

## How to run (throwaway stack, port/DB isolated)

```bash
# 1) throwaway Postgres on :5433
docker run -d --name moldy-e2e-pg -p 5433:5432 \
  -e POSTGRES_DB=moldy -e POSTGRES_USER=moldy -e POSTGRES_PASSWORD=moldy postgres:16-alpine

# 2) backend/.env: throwaway DB + generated ENCRYPTION_KEYS/JWT_SECRET + E2E flags
#    DATABASE_URL=...@localhost:5433/moldy  (+ _SYNC), E2E_SCRIPTED_MODEL_ENABLED=true,
#    E2E_SEED_USER_ENABLED=true, E2E_TEST_HELPERS_ENABLED=true, RATE_LIMIT_ENABLED=false

# 3) migrate the throwaway DB
cd backend && uv run alembic upgrade head

# 4) run (Playwright boots backend+frontend itself; or reuse a running backend)
cd frontend && E2E_FRONTEND_PORT=3100 E2E_BACKEND_PORT=8101 \
  pnpm exec playwright test --grep-invert "Manual Atlassian"
```

## Run model

- **One mode: live throwaway backend + the seeded `e2e_scripted` model.** Chat
  flows run a real start→stream→create turn deterministically with **no LLM API
  key** (the scripted model is keyless — `credential_resolution` bypasses it).
- **Mock-only specs** layer `page.route` on top of the live backend. They must
  mock *every* endpoint the page touches — an unmocked call hits the real backend
  with fixture IDs and 422s, which the `errors` fixture flags as a console error.
- `PW_SKIP_BACKEND=1` is an optional pure-frontend mock mode (no backend booted).
- The seeded E2E user is a **super_user**, so operator-only screens (system
  credentials, system LLM, marketplace moderation, audit) are testable.

## Status legend

| | meaning |
|---|---|
| ✅ | real — drives the live backend through the UI |
| 🟨 | mock — `page.route` fixtures (determinism-required) |
| 🟦 | smoke — page loads / dialog opens only, no flow |
| ❌ | none |
| 🔒 | manual-only (real third-party OAuth / external process) |

## Matrix

| Feature area | Route(s) | Router | Spec | Status |
|---|---|---|---|---|
| Session bootstrap | — | `auth` | `e2e/global-setup.mjs` (API login) | ✅ infra |
| Signup / login / logout UI | `(auth)/login`, `(auth)/register` | `auth` | `auth` | ✅ |
| Profile personalization | `/settings` | `auth` | — | ❌ |
| Dashboard / static pages | `/`, `/tools`, `/models`, `/usage` | — | `smoke` | 🟦 |
| Agent creation pages | `/agents/new{,/manual,/template,/conversational}` | `agents`, `builder` | `smoke` | 🟦 |
| Conversational builder (real flow) | `/agents/new/conversational` | `builder` | `builder` | ✅ |
| Agent settings — system prompt edit | `/agents/[id]/settings` | `agents` | `agent-settings` | ✅ |
| Attach sub-agent | `/agents/[id]/settings` | `agent_subagents` | `agent-settings` | ✅ |
| Attach skill | `/agents/[id]/settings` | `agent_skills` | `agent-settings` | ✅ |
| Attach tool | `/agents/[id]/settings` | `agent_tools` | `agent-settings` | ✅ |
| Attach MCP tool | `/agents/[id]/settings` | `agent_mcp_tools` | — | ❌ (needs an MCP server) |
| **Subagent delegation run** | chat | `agents`, agent-runtime | — | ❌ |
| Chat run lifecycle | `/agents/[id]/conversations/[cid]` | `conversation_runs` | `chat-run-lifecycle` | ✅ |
| Chat navigator + sort/view | sidebar | `conversations` | `chat-navigator`, `chat-navigator-live`, `smoke` | ✅ |
| Draft conversation | `/conversations/new` | `conversations` | `draft-conversation` | ✅ |
| Token usage hover | chat | `conversation_messages` | `chat-token-usage` | 🟨 |
| Document artifacts | chat | `artifacts` | `document-artifact-viewers` | ✅ |
| **Branching / HITL / msg actions / attachments** | chat | `conversation_branches`, `feedback`, `uploads` | — | ❌ |
| Credentials (user) | `/credentials` | `credentials` | `credentials` | ✅ (create only) |
| Skills | `/skills` | `skills` | `skills-management` | ✅ (create only) |
| Tools | `/tools` | `tools` | `tools-catalog` | ✅ (create only) |
| Models (discover/ranking/test/fallback) | `/models` | `models` | `models-discover`, `model-ranking`, `model-test`, `model-fallback` | 🟨 |
| MCP registry / wizard / health | `/mcp-servers` | `mcp`, `health` | `mcp-registry`, `mcp-server-wizard`, `health-check` | ✅/🟨 |
| MCP Atlassian OAuth | `/mcp-servers` | `mcp` | `manual-atlassian-oauth` | 🔒 |
| Spend dashboard | `/usage` | `usage` | `spend-dashboard` | 🟨 |
| Schedules / triggers | `/agents/[id]/settings` (schedule tab) | `triggers` | `agent-triggers` | ✅ (create + render) |
| Public share link | `/shared/[id]` | `shares` | `share-link` | ✅ |
| Marketplace browse + install | `/marketplace` | `marketplace` | `marketplace` | ✅ (install via API) |
| Marketplace publish/moderation | `/marketplace/admin` | `marketplace` | — | ❌ |
| **Memory controls** | `/settings/memory` | `memory` | — | ❌ |
| **Agent API deployment** | `/settings/agent-api` | `agent_api` | — | ❌ |
| **Audit trail** | `/settings/audit` | `audit` | — | ❌ |
| **System credentials / System LLM** | `/settings/system-*` | `credentials`, `system_llm_settings` | — | ❌ |

## Build plan (priority order)

**P1 — core journeys + recent untested features**
1. Auth UI: signup → logout → login (entry point, low cost)
2. Conversational builder → agent created (scripted model)
3. Agent settings: edit system prompt + attach/detach skill, MCP, tool, subagent
4. Subagent delegation run (scripted)
5. Marketplace: publish → install (PR #248, zero coverage)

**P2 — remaining feature areas**
6. Schedule create → run → history
7. Public share link (read-only)
8. Chat branching (edit/regenerate) + HITL approval
9. Agent API deployment + key
10. Memory controls · audit trail · system-credentials · system-LLM settings

**Quality pass**
- Keep mocks only where determinism requires (token usage, 3rd-party errors).
  Everything else should drive the live throwaway backend via the scripted model.

## Gotchas

- **CSRF in test-body POSTs.** `beforeAll`'s `request` is worker-scoped; a
  `test`'s `request` is test-scoped with a different `moldy_csrf` cookie. Doing a
  mutating call (POST/DELETE) in a test body needs a fresh `login(request)` in
  that test so the `X-CSRF-Token` header matches the context's cookie. GETs are
  fine (no CSRF). This is why most specs do their writes in `beforeAll`.
- **Triggers need `identity_mode: 'fixed'`** on the agent (credential then comes
  from the model default).
- **Text skills** need SKILL.md frontmatter (`name:`) in `content` to create.

## Constraints

- **Builder & subagent delegation need a real LLM.** The conversational builder
  and the Assistant use the `builder_*`/`assistant_*` model (default Anthropic),
  NOT the keyless `e2e_scripted` model. To make those flows fully real, either
  (a) provide a real key in the throwaway stack and point System LLM settings at
  it, or (b) extend scripted-model support to builder/subagent flows. Until then
  they stay ❌ / best-effort (page-load + session-start only).
- Everything that does not require an LLM *decision* (CRUD, attach/detach,
  navigation, schedules, share, marketplace install) is fully real via the live
  throwaway backend.

## Changelog

- Added `auth` spec: login (success + wrong-credentials error), signup
  (new user auto-login), logout from the user menu. 4 tests, real backend.
- Added real-LLM provisioning via `seed.e2e_llm` (E2E_LLM_* env → openai_compatible
  system + user credentials, Model, System LLM text_primary/fallback). Verified a
  real chat turn POSTs to the gateway (200) on a freshly-recreated DB.
- Added `agent-settings` spec: edit system prompt + save, attach a sub-agent +
  save. Both verified persisted via the API. 2 tests. (tool/skill/MCP attach next —
  the tools dialog distinguishes create-new from attach-existing.)
- Added `builder` spec: `?initialMessage=` drives the conversational builder; it
  creates a session and runs the multi-phase pipeline against the real System LLM
  (LiteLLM). Asserts the template-driven progress tracker (stable), not LLM prose.
- Extended `agent-settings` with skill attach (3 tests total). The tools/skills
  dialog attaches existing items via a per-row "{name} 추가" button; text skills
  need SKILL.md frontmatter (`name:`) in their content to create via the API.
- Added `share-link` spec: send a message, publish, open `/shared/{token}` in a
  logged-out context (read-only), then revoke -> public GET 404s.
- Added `marketplace` spec: catalog renders seeded skills; installing one creates
  an independent copy (`installed_skill_id`); the "설치됨" tab reflects it.
- Extended `agent-settings` with tool attach (4 tests): `tavily_search` needs no
  per-tool credential, so it's the easiest attachable tool; My Tools tab uses the
  same per-row "{name} 추가" button as Skills.
- Added `agent-triggers` spec: create an interval trigger via the API, verify it
  renders in the settings → 스케줄 tab ("매 N분"). Gotcha: triggers require the
  agent to be `identity_mode: 'fixed'` (AGENT_IDENTITY_REQUIRES_FIXED); the
  credential is then resolved from the model's default. AgentCreate has no
  `llm_credential_id` field — bind via the model default or a separate call.

- Stood up runnable throwaway stack; baseline 41✅ / 4✗ / 8 skipped.
- Fixed `draft-conversation` (×3): navigator control is now `button "새 채팅"`
  (was `button "새 대화"`); switched the agent to the keyless scripted model so
  the first-message flow is deterministic.
- Fixed `chat-token-usage` (×1): added the missing `GET /api/conversations/{id}`
  mock (the page fetched conversation detail → 422 with the live backend).
- Removed redundant `frontend/pnpm-workspace.yaml` (duplicated root's
  `ignoredBuiltDependencies`) that broke all `pnpm`-from-frontend commands,
  including the documented `cd frontend && pnpm test:e2e`, on pnpm 9.15.
