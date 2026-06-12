<div align="center">

<img src="docs/images/moldy-mascot.webp" alt="Moldy mascot" width="160">

# Moldy

**A no-code AI agent builder you talk to — FastAPI + LangGraph + deepagents**

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)]()
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg)]()
[![React](https://img.shields.io/badge/React-19-61dafb.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)]()
[![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-purple.svg)]()
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[한국어](README_KO.md) · English · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[Overview](#-overview) · [Quick Answers](#-quick-answers) · [Quick Start](#-quick-start) · [Trust](#-quality-security-and-documentation-signals) · [Features](#-features) · [Architecture](#-architecture)

**Last updated:** June 7, 2026 · **Repository:** [YooSuhwa/natural-mold](https://github.com/YooSuhwa/natural-mold) · **License:** [MIT](LICENSE)

</div>

---

## 🧐 Overview

**Moldy** is a no-code AI agent builder you configure by *talking* instead of
filling in forms. Describe what you want in natural language and a meta-agent
assembles the tools, skills, and triggers for you. You can then chat with the
resulting agent or schedule it to run on its own.

### What Is Moldy?

Moldy is an open-source, self-hostable AI agent builder for creating, configuring,
chatting with, and scheduling AI agents from a web UI. The project combines a
Next.js 16 + React 19 frontend, a FastAPI backend, PostgreSQL 16, LangGraph 1.x,
and the `deepagents` `create_deep_agent` runtime. Moldy is built for multi-user
operation: ADR-016 added JWT auth, HttpOnly cookies, CSRF double-submit
protection, refresh-token rotation, and a `super_user` role for system resources.
The monorepo includes chat streaming, message branching, credential management,
MCP server integration, skill packages, marketplace installation, scheduled
triggers, and usage tracking.

### Project Facts

| Fact | Current README State |
|---|---|
| Project type | Open-source web application and monorepo |
| Primary use case | No-code AI agent creation, chat, scheduling, and tool/skill orchestration |
| Backend | FastAPI 0.115+, SQLAlchemy 2.0 async, Alembic, Python 3.12 |
| Frontend | Next.js 16, React 19, TailwindCSS v4, shadcn/ui |
| AI runtime | LangGraph 1.x + `deepagents` via `create_deep_agent` |
| Database | PostgreSQL 16, current migration head `m59_conversation_artifacts` |
| Authentication | JWT HS256, HttpOnly cookies, CSRF double-submit, refresh-token rotation, `super_user` |
| License | MIT |

### What's different

- **Conversational builder** — A meta-agent interviews you about your intent,
  proposes build options step by step, and only commits to creating the agent
  once you agree. **Describe requirements** instead of filling out a long form.
- **Unified tool / skill / MCP catalog** — Manage prebuilt tools (web search,
  scraper, Gmail, Calendar, ...), registry-backed **MCP servers** (stdio / SSE /
  Streamable HTTP), and user-defined **Skills** (a `SKILL.md` plus auxiliary
  files) from a single UI.
- **Branching conversations** — Built on the LangGraph checkpointer so editing
  a user message or regenerating an assistant reply forks a new branch.
  `<N/M>` arrows let you flip between sibling responses.
- **Human-in-the-Loop** — Tool-call approvals, user-input prompts, and
  clarifying-question interrupts come with a **countdown timer + auto-extend**
  UX that promotes urgency without forcing you to babysit the agent.
- **No-code triggers** — Cron / interval schedules run agents at chosen times
  and pipe the result to a notification channel (Google Chat webhook, etc.).
- **Public share links** — One click turns a conversation into a read-only
  link anyone can open without signing in to follow the agent's reasoning.

## ❓ Quick Answers

### What Does Moldy Do?

Moldy turns natural-language requirements into runnable AI agents. A user can
describe a workflow, let the conversational builder propose an agent
configuration, attach tools, skills, MCP tools, and credentials, then run that
agent in chat or through cron/interval triggers. The app supports branchable
conversations, SSE streaming, tool-call approval flows, public read-only share
links, and per-user credential isolation.

### Who Is Moldy For?

Moldy is for developers, operators, and internal-tool teams that want a local or
self-hosted agent builder rather than a fully managed SaaS-only workflow. The
README assumes the reader can run PostgreSQL, Python 3.12, Node 22, `uv`, and
`pnpm`, while the product UI is designed so non-coding users can assemble agents
through guided setup, credentials, tools, skills, and schedules.

### How Does Moldy Handle Credentials and System Access?

Moldy separates operator-managed system resources from per-user resources.
System credentials and System LLM settings are managed by `super_user` accounts,
while ordinary users register personal credentials at `/credentials`. Credential
payloads are encrypted with Cipher V2, using HKDF-SHA256 and AES-256-GCM, and
runtime access is mediated through explicit tool, model, MCP, and skill bindings.

### What Claims Can Be Verified in This Repository?

Moldy's architecture and security claims are backed by repository-local evidence.
Architecture decisions live in [`docs/design-docs/`](docs/design-docs/), the
high-level system map lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
security reporting and deployer hardening live in [`SECURITY.md`](SECURITY.md),
and repeatable verification commands are listed in this README. The backend and
frontend also include test suites that are exercised by the documented commands
and the pre-push hook.

## 🚀 Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager; also provisions Python 3.12 for the backend
- [Node.js 22](https://nodejs.org/) + [pnpm](https://pnpm.io/) — frontend runtime + package manager
- [Docker](https://www.docker.com/) — for the PostgreSQL 16 container
- An LLM API key — one of OpenAI / Anthropic / OpenRouter / OpenAI-compatible (e.g. LiteLLM). No need to put it in ENV; **register it in the UI after boot** (ADR-013)

### Local development

```bash
# 1. Start PostgreSQL
docker compose up postgres -d         # localhost:5432, moldy:moldy/moldy

# 2. Backend (uv downloads Python 3.12 automatically)
cd backend
cp .env.example .env                  # set ENCRYPTION_KEYS / JWT_SECRET (LLM keys via UI)
uv sync                               # install dependencies (+ Python 3.12 if missing)
uv run alembic upgrade head           # run migrations (head: m59)
uv run uvicorn app.main:app --reload --reload-dir app --port 8001
# → http://localhost:8001/docs (Swagger UI)

# 3. Frontend (new terminal, Node 22)
cd frontend
cp .env.example .env.local            # NEXT_PUBLIC_API_BASE_URL / E2E account defaults
pnpm install
pnpm dev
# → http://localhost:3000
```

The first run seeds default models (GPT-5.5, Claude Sonnet 4.6, Gemini, ...),
system tools, agent templates, and the local Playwright E2E account. However,
**operator setup below is required before you can build and use agents**.

### Post-boot setup (operator)

LLM keys are registered in the UI (not ENV), and system features (builder,
assistant, image generation) require the operator to pick which models to use
(ADR-013/016/019).

1. **First account = operator** — Sign up at http://localhost:3000. The first
   user is auto-promoted to `super_user` (ADR-016, `ALLOW_FIRST_USER_AS_ADMIN=true`;
   turn it off after the operator account exists in production).
2. **Register LLM credentials** — At `/settings/system-credentials`, add OpenAI ·
   Anthropic · OpenRouter · OpenAI-compatible (e.g. LiteLLM) keys.
3. **Pick System LLM models (ADR-019, required)** — At `/settings/system-llm`,
   choose a model for each of the `text_primary` · `text_fallback` · `image`
   slots (select credential → "Load models" → pick a model). **Until this is
   configured, the builder, assistant, and image generation will not work**
   (explicit error, no silent failure).
4. **Wire models for agents** — At `/models`, attach a credential to the models
   your agents will use, or auto-register them via discovery.

Then build agents through the conversational builder (`/agents`) and chat. Regular
users register their own keys at `/credentials`.

### Worktree dev port / CORS rules

When working in a git worktree, run `bash scripts/worktree-setup.sh` first so that
`backend/.env` and `backend/data` point at the main checkout via symlinks. Sharing
the same PostgreSQL, `ENCRYPTION_KEYS`, and `JWT_SECRET` keeps existing credential
decryption and login sessions from breaking.

The backend/frontend dev servers must keep **frontend port, backend port, CORS
origin, and `NEXT_PUBLIC_API_BASE_URL` as one matched set**. Recommended default:

```bash
# backend
cd backend
uv run uvicorn app.main:app --reload --reload-dir app --port 8001

# frontend
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

To run several worktrees at once, pin the port pairs explicitly:

```bash
# backend (:8010)
cd backend
CORS_ALLOWED_ORIGINS=http://localhost:3010,http://127.0.0.1:3010 \
  uv run uvicorn app.main:app --reload --reload-dir app --port 8010

# frontend (:3010)
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010 pnpm dev -- --port 3010
```

Always fix the port with `pnpm dev -- --port <port>` — if Next.js picks a random
port on conflict, CORS / cookies / CSRF can drift. Attaching multiple backends to
the same DB can double-run APScheduler/trigger jobs, so be careful with long
concurrent sessions.

### Run everything with Docker Compose

Compose reads secrets from `backend/.env`, runs `alembic upgrade head` inside the
backend container before it serves, and persists `data/` in a named volume.

```bash
cp backend/.env.example backend/.env  # set ENCRYPTION_KEYS / JWT_SECRET
docker compose up -d                  # postgres + backend (migrate → serve) + frontend
# Then follow "Post-boot setup" above for operator onboarding.
```

Deploying to a remote host (not localhost)? `NEXT_PUBLIC_API_BASE_URL` is inlined
into the frontend bundle at build time, so set it before the build and allow the
new origin in CORS:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
CORS_ALLOWED_ORIGINS=https://app.example.com \
  docker compose up -d --build
```

### Verification commands

```bash
# Backend
cd backend
uv run ruff check .                   # lint
uv run pytest                         # unit tests (aiosqlite, no Postgres needed)
uv run pytest -m integration          # integration tests (Postgres required)

# Frontend
cd frontend
pnpm lint                             # ESLint
pnpm exec tsc --noEmit                # type check
pnpm test --run                       # vitest (jsdom)
pnpm build                            # production build
pnpm test:e2e                         # Playwright E2E
```

## ✅ Quality, Security, and Documentation Signals

Moldy documents its technical decisions and operational risks inside the
repository, so readers can verify the README's claims without relying on
unsupported positioning copy. The strongest trust signals are the ADR record,
the explicit security policy, the reproducible test commands, and the local
operator setup instructions. For E-E-A-T, this README exposes implementation
experience through setup details, expertise through architecture and ADR links,
authoritativeness through repository evidence, and trust through security and
verification workflows.

| Signal | Evidence | Why It Matters |
|---|---|---|
| Architecture decisions | [`docs/design-docs/`](docs/design-docs/) includes ADR-016 for multi-user auth and ADR-019 for System LLM settings | Shows when and why major runtime, auth, credential, and UI decisions were made |
| System architecture | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) maps the Next.js frontend, FastAPI backend, PostgreSQL data layer, and LangGraph/deepagents runtime | Gives reviewers a traceable design reference beyond the README |
| Security process | [`SECURITY.md`](SECURITY.md) documents private vulnerability reporting, response targets, and deployer hardening checks | Makes security reporting and production responsibilities explicit |
| Verification workflow | This README lists backend lint/tests, frontend lint/typecheck/tests/build, integration tests, and Playwright E2E commands | Lets maintainers and adopters reproduce the validation path |
| Operational setup | The Quick Start separates local dev, worktree CORS rules, Docker Compose, E2E seed auth, System LLM setup, and MCP registry setup | Reduces ambiguity for self-hosted or multi-worktree development |

### Playwright E2E auth

E2E does not log in through the form per test. Instead, Playwright's global setup
performs one API login, then injects the resulting `storageState` into every
browser context. `backend/.env.example` ships `E2E_SEED_USER_ENABLED=true` for
local dev, so the backend creates/refreshes the dummy super_user below on startup.
This seed is skipped automatically when `APP_ENV=production`.

```bash
E2E_USER_EMAIL=playwright-e2e@moldy.dev
E2E_USER_PASSWORD=correct horse battery staple 42
E2E_USER_NAME=E2E User
```

The frontend env file uses the same dedicated test account:

```bash
cd frontend
cp .env.example .env.local
# adjust E2E_USER_EMAIL / E2E_USER_PASSWORD if needed
pnpm test:e2e
```

The recommended flow is `login → register fallback → login → save to
e2e/.auth/user.json`. `frontend/e2e/.auth/` is generated output and is not
committed. E2E setup code that creates/updates via the API must pass the login
response's `csrf_token` as an `X-CSRF-Token` header.

> **Pre-push hook**: `git push` triggers `.husky/pre-push`, which runs backend
> pytest + frontend vitest. Failing tests block the push so regressions cannot
> reach the remote. Bypass with `git push --no-verify` for WIP branches only.

### Tavily + Deep Research

The Tavily hosted search tool (`tavily_search`) is wired to the Deep Research
marketplace skill. Set `TAVILY_API_KEY` in the backend `.env` and the Deep
Research skill **auto-injects `tavily_search` as a runtime tool dependency**, so
it runs citation-backed multi-step web research without the user attaching any
tool manually. (Design background:
`docs/superpowers/plans/2026-05-31-deep-research-tavily.md`)

### MCP Registry and MCP Secret

At `/mcp-servers` -> **New MCP Server**, choose a registry preset to pre-fill
transport, URL, and stdio command/env templates, then run a **tool probe**
before saving to see what the server actually exposes. Current presets include
GitHub, Linear, Atlassian Jira, Slack, Notion, and local first-party MCP servers
(Hancom Groupware, Hancom Mile Meeting, Hancom Org Chart, Maepsi).

For first-party MCP presets that require auth, create an `MCP Secret` credential
at `/credentials`, then attach it in the wizard's **Auth** tab. Moldy forwards
the `secret` value as the `X-Moldy-Credential` header during connection and
runtime execution. For manually registered MCP servers, headers and stdio env
vars can interpolate the attached credential with `{{ $credentials.<field> }}`.

Local first-party MCP presets default to the `localhost:18001`-`18004` range.
Those servers are not included in `docker compose up`, so start the MCP server
process separately before probing those presets.

## 📸 Screenshots

> Coming soon. Screen wireframes are documented in `docs/PRD-screens.md`.

## ✨ Features

<details>
<summary><b>🤖 Agent system</b></summary>

- **deepagents engine** — `create_deep_agent` over a compiled LangGraph that
  manages the message tree, branches, and checkpoints
- **Conversational builder** — Meta-agent interviews requirements and proposes
  build options (`agent_runtime/builder_v3/`)
- **Agent templates** — Pre-built agents you can spawn instantly
- **Sub-agents** — Multi-level delegation (an agent invokes another agent as a tool)
- **Middleware system** — 22 middlewares across context engineering, planning,
  safety, reliability, and provider-specific categories
- **Model fallback chain** — Up to 5 fallback models if the primary call fails

</details>

<details>
<summary><b>💬 Chat + branching</b></summary>

- **SSE streaming** — Token-level live output with tool-call visualization;
  plain code-block rendering while streaming + O(1) SSE queue keep long replies fast
- **IME-safe composer** — Korean and other composition-based input stays intact
  across Enter, edit, and regenerate flows while composer state syncs safely
- **LangGraph fork** — Editing a user message or regenerating an assistant turn
  forks a new branch; checkpoint IDs power "time travel"
- **BranchPicker** — `<N/M>` arrows compare sibling responses (assistant-ui integration)
- **HITL countdown** — Timer + auto-extend + urgent-state styling for tool
  approvals, user-input requests, and clarifying questions
- **Message actions** — Copy, edit, regenerate, thumb feedback, delete, search
- **Markdown surface** — Mermaid diagrams, KaTeX math, code blocks, image lightbox
- **Attachments** — Inline image / document uploads embedded into messages
- **Public share links** — Read-only `/shared/{token}` page; soft-deleting the
  link invalidates it instantly

</details>

<details>
<summary><b>🛠️ Tools · Skills · MCP</b></summary>

- **Built-in tool catalog** — DuckDuckGo, web scraper, current time, relative-date
  resolver (`resolve_relative_date`), Tavily search, Naver search (5), Google CSE (3),
  Gmail send, Google Calendar, Google Chat webhook, HTTP request
- **MCP integration** — Register stdio + SSE + Streamable HTTP servers via
  `langchain-mcp-adapters`, with import / export and health-check polling
- **MCP registry presets** — Pick GitHub / Linear / Jira / Slack / Notion /
  Hancom / Maepsi servers in the `/mcp-servers` wizard and probe tools before saving
- **MCP Secret credential** — Automatically forwards a per-user secret to
  first-party MCP servers via the `X-Moldy-Credential` header
- **Skill system** — `SKILL.md` (YAML frontmatter) plus auxiliary files; inline
  multi-file editor; create skills from scratch, upload, or import
- **Skill runtime dependencies** — Tools a skill declares are auto-injected at
  agent runtime (e.g. Deep Research → Tavily); no manual tool attachment needed
- **Custom tools** — Define tool parameters with Pydantic schemas

</details>

<details>
<summary><b>🔐 Credentials · model management</b></summary>

- **Cipher V2 encryption** — HKDF-SHA256 + AES-256-GCM single-blob Base64
- **Vault integration** — `hvac`-based external secrets
- **System / user split** — Operator-managed credentials vs. per-user keys
- **MCP Secret** — Per-user secret credential for local first-party MCP servers
- **Korean service integrations (8 types)** — SRT · KTX · Forest Trip · KIPRIS · DART · ODsay · Coupang Partners · K-Skill Proxy
- **Model discovery** — Probe LLM APIs through a credential to auto-pull the
  available model list, pricing, and context window
- **Model health checks** — Periodic probes monitor reachability
- **Benchmark rankings** — Surface LMArena, LiveBench, AAIndex scores

</details>

<details>
<summary><b>⏰ Triggers · usage · observability</b></summary>

- **Schedule triggers** — APScheduler-backed cron / interval, per-agent input
  message, Google Chat webhook notifications
- **Schedule guardrails** — Max run count (`max_runs`), end time (`end_at`),
  auto-pause after consecutive failures (`auto_pause_after_failures`)
- **Conversation policy** — Each trigger can start a fresh conversation or reuse a target one
- **Run history** — `agent_trigger_runs` records per-run source / output preview /
  duration / thread · checkpoint · trace IDs
- **Token usage tracking** — Per-agent / per-model / daily token + estimated cost
- **Daily spend** — Roll-ups by user / agent / model
- **Tracing** — LangSmith auto-forwarding + Langfuse external traces
  (provider / id / url recorded on `message_events`)

</details>

<details>
<summary><b>🎨 Frontend</b></summary>

- **Next.js 16 + React 19** — App Router, Server Components first
- **TailwindCSS v4 + shadcn/ui** — Token-based design (`--primary-strong` emerald),
  per ADR-010
- **DialogShell pattern** — Every dialog uses size tokens (`md`/`lg`/`xl`/`console`);
  `srOnly` header prop for lightbox-style dialogs
- **TanStack Query** — Server state with caching + invalidation
- **Jotai** — Client state (sidebar, right rail, etc.)
- **assistant-ui** — Chat message tree, BranchPicker, ActionBar
- **i18n** — Powered by next-intl, Korean as the default locale
- **Responsive** — Mobile sidebar uses Sheet; desktop uses SidebarProvider

</details>

<details>
<summary><b>🛒 Marketplace</b></summary>

- **Catalog** — Publish Agents, MCP servers, and Skills to a shared marketplace; install with one click
- **Publish / install separation** — Installing creates an independent copy in your account, decoupled from the original
- **Version snapshots** — `marketplace_versions` stores an immutable history of every published version
- **Credential binding** — Map skill-required credentials to your own keys at install time
- **Tool dependency surfacing** — The install wizard shows tools a skill needs
  (e.g. Tavily) and auto-injects them at runtime
- **Moderation** — super_user reviews submissions at `/marketplace/admin/moderation`

</details>

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                      │
│  app/ (routes) → components/ (UI) → lib/api,hooks,stores        │
│  ↓ fetch + SSE (EventSource)                                   │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                       Backend (FastAPI)                         │
│  routers/ → services/ → models/ (SQLAlchemy 2.0 async)         │
│                                                                 │
│  agent_runtime/                                                 │
│    ├ builder_v3/ (conversational meta builder — latest)         │
│    ├ executor.py (compat facade)                                │
│    ├ runtime_component_builder.py (models/tools/skills/memory)  │
│    ├ agent_stream_runner.py (stream/invoke execution)           │
│    ├ streaming.py (LangGraph events → SSE + traces + artifacts) │
│    ├ mcp_tool_loader.py / skill_executor.py                     │
│    └ trigger_executor.py (schedule → invoke)                    │
│                                                                 │
│  scheduler.py — APScheduler singleton                           │
└─────────────────────────────────────────────────────────────────┘
                  ↓                              ↓
       PostgreSQL (models / chats / tools)  LangGraph PostgresSaver
                                            (checkpoints = message tree)
```

### Three-tier backend

- **Router** (`app/routers/`) — HTTP endpoints, request / response shaping
- **Service** (`app/services/`) — Business logic, DB queries, transactions
- **Model** (`app/models/`) — SQLAlchemy ORM, 49 tables as of m59

### Frontend pattern

- API client (`lib/api/`) → TanStack Query hooks (`lib/hooks/`) → components
- Chat SSE flows through an EventSource wrapper in `lib/sse/`
- Design tokens live in `lib/design-tokens.ts` + `app/globals.css` (oklch-based)

See [`CLAUDE.md`](CLAUDE.md) for the developer handbook with deeper conventions.

## 📁 Project structure

```
natural-mold/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory + lifespan
│   │   ├── config.py            # pydantic-settings (.env)
│   │   ├── database.py          # async engine + session
│   │   ├── dependencies.py      # get_db, get_current_user, require_super_user, verify_csrf
│   │   ├── scheduler.py         # APScheduler singleton
│   │   ├── models/              # SQLAlchemy ORM
│   │   ├── schemas/             # Pydantic schemas
│   │   ├── routers/             # HTTP routers
│   │   ├── services/            # business logic
│   │   ├── credentials/         # Cipher V2 + domain
│   │   ├── agent_runtime/       # AI execution engine
│   │   └── seed/                # seed data
│   ├── alembic/versions/        # migrations (up to m59)
│   └── tests/                   # pytest (aiosqlite in-memory)
├── frontend/
│   └── src/
│       ├── app/                 # Next.js App Router (23+ routes)
│       ├── components/          # UI components
│       └── lib/                 # api, hooks, stores, sse, types
├── docs/
│   ├── PRD.md                   # product requirements
│   ├── PRD-screens.md           # screen wireframes
│   ├── ARCHITECTURE.md          # system architecture
│   ├── design-docs/             # ADRs (design decisions)
│   ├── marketplace-resources-prd.md  # marketplace PRD
│   └── tool-setup-guide.md      # tool API key setup
├── tasks/                       # working notes + archive/
├── docker-compose.yml
├── HANDOFF.md                   # session handoff doc
├── TASKS.md                     # phased task tracker
├── CLAUDE.md                    # developer handbook
├── CONTRIBUTING.md
└── SECURITY.md
```

## 🔧 Environment variables

See `backend/.env.example` for the full list. Minimum keys to boot:

| Variable | Required | Description |
|------|------|------|
| `DATABASE_URL` | yes | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `DATABASE_URL_SYNC` | yes | PostgreSQL sync URL (`postgresql://...`) — used by the LangGraph checkpointer; **not derived** from `DATABASE_URL`, so set both when changing the DB host |
| `ENCRYPTION_KEYS` | yes | Cipher V2 master key(s) — comma-separated 64-char hex, first is active (HKDF-SHA256 + AES-256-GCM). Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_SECRET` | yes | JWT HS256 signing key (ADR-016 multi-user auth) |
| LLM keys (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, …) | optional | Register via UI Credentials (ADR-013). ENV is an optional dev bootstrap |
| `OPENROUTER_API_KEY` | optional | Agent image generation (OpenRouter + Gemini Flash Image) |
| `LANGSMITH_API_KEY` | optional | LangSmith tracing |
| `TAVILY_API_KEY` | optional | Hosted key for Tavily search / Deep Research skill |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | optional | Naver search tools |
| `GOOGLE_API_KEY` / `GOOGLE_CSE_ID` | optional | Google CSE tools |
| Google OAuth2 token | optional | Gmail / Calendar tools (`scripts/google_oauth_setup.py`) |

Per-tool key setup is documented in [`docs/tool-setup-guide.md`](docs/tool-setup-guide.md).

## 🧩 Structured Data (JSON-LD)

Moldy can use the following JSON-LD on a project homepage, documentation site,
or product page that republishes this README. GitHub README rendering does not
execute JSON-LD, so place this block in a server-rendered
`<script type="application/ld+json">` element on the actual web page. The schema
uses only repository-visible facts; add additional `sameAs` links only after
official profiles or documentation URLs exist.

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://github.com/YooSuhwa/natural-mold#organization",
      "name": "Moldy contributors",
      "url": "https://github.com/YooSuhwa/natural-mold",
      "sameAs": [
        "https://github.com/YooSuhwa/natural-mold"
      ],
      "description": "Moldy contributors maintain an open-source, self-hostable AI agent builder for creating, chatting with, and scheduling AI agents.",
      "knowsAbout": [
        "AI agent builders",
        "LangGraph",
        "deepagents",
        "FastAPI",
        "Next.js",
        "Model Context Protocol",
        "credential encryption",
        "agent scheduling"
      ]
    },
    {
      "@type": "SoftwareApplication",
      "@id": "https://github.com/YooSuhwa/natural-mold#software",
      "name": "Moldy",
      "url": "https://github.com/YooSuhwa/natural-mold",
      "description": "Moldy is an open-source, self-hostable no-code AI agent builder for creating, configuring, chatting with, and scheduling AI agents from a web UI.",
      "applicationCategory": "DeveloperApplication",
      "operatingSystem": "Web",
      "isAccessibleForFree": true,
      "license": "https://github.com/YooSuhwa/natural-mold/blob/main/LICENSE",
      "softwareVersion": "development snapshot, migration head m59",
      "dateModified": "2026-06-07",
      "author": {
        "@id": "https://github.com/YooSuhwa/natural-mold#organization"
      },
      "publisher": {
        "@id": "https://github.com/YooSuhwa/natural-mold#organization"
      },
      "offers": {
        "@type": "Offer",
        "price": "0",
        "priceCurrency": "USD"
      },
      "softwareRequirements": [
        "Python 3.12",
        "Node.js 22",
        "PostgreSQL 16",
        "Docker",
        "uv",
        "pnpm"
      ],
      "featureList": [
        "Conversational AI agent builder",
        "LangGraph and deepagents runtime",
        "MCP server registry and tool import",
        "Skill package management",
        "JWT and HttpOnly cookie authentication",
        "Cipher V2 credential encryption",
        "SSE chat streaming",
        "Branchable conversations",
        "Cron and interval agent triggers",
        "Marketplace installation for skills"
      ]
    },
    {
      "@type": "SoftwareSourceCode",
      "@id": "https://github.com/YooSuhwa/natural-mold#source-code",
      "name": "Moldy source code",
      "codeRepository": "https://github.com/YooSuhwa/natural-mold",
      "programmingLanguage": [
        "Python",
        "TypeScript"
      ],
      "runtimePlatform": [
        "Python 3.12",
        "Node.js 22",
        "PostgreSQL 16"
      ],
      "license": "https://github.com/YooSuhwa/natural-mold/blob/main/LICENSE",
      "targetProduct": {
        "@id": "https://github.com/YooSuhwa/natural-mold#software"
      }
    },
    {
      "@type": "FAQPage",
      "@id": "https://github.com/YooSuhwa/natural-mold#faq",
      "mainEntity": [
        {
          "@type": "Question",
          "name": "What does Moldy do?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy turns natural-language requirements into runnable AI agents that can use tools, skills, MCP tools, credentials, chat streaming, and scheduled triggers."
          }
        },
        {
          "@type": "Question",
          "name": "How does Moldy protect credentials and system access?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy separates super_user-managed system resources from per-user resources, uses JWT auth with HttpOnly cookies and CSRF protection, and encrypts credential payloads with Cipher V2 using HKDF-SHA256 and AES-256-GCM."
          }
        },
        {
          "@type": "Question",
          "name": "What technology stack does Moldy use?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy uses Next.js 16, React 19, TailwindCSS v4, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, LangGraph 1.x, and deepagents create_deep_agent."
          }
        }
      ]
    }
  ]
}
```

## 🤝 Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues should follow the
process in [`SECURITY.md`](SECURITY.md).

## 📄 License

[MIT](LICENSE) — Copyright (c) 2026 Moldy contributors.

---

<div align="center">

For deeper conventions, design tokens, and long-horizon workflows see
[`CLAUDE.md`](CLAUDE.md) and [`frontend/AGENTS.md`](frontend/AGENTS.md).

</div>
