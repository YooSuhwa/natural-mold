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

[한국어](README.md) · [English](README_EN.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[Overview](#-overview) · [Quick Start](#-quick-start) · [Features](#-features) · [Architecture](#-architecture)

</div>

---

## 🧐 Overview

**Moldy** is a no-code AI agent builder you configure by *talking* instead of
filling in forms. Describe what you want in natural language and a meta-agent
assembles the tools, skills, and triggers for you. You can then chat with the
resulting agent or schedule it to run on its own.

### What's different

- **Conversational builder** — A meta-agent interviews you about your intent,
  proposes build options step by step, and only commits to creating the agent
  once you agree. **Describe requirements** instead of filling out a long form.
- **Unified tool / skill / MCP catalog** — Manage prebuilt tools (web search,
  scraper, Gmail, Calendar, …), external **MCP servers** (stdio / HTTP), and
  user-defined **Skills** (a `SKILL.md` plus auxiliary files) from a single UI.
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

## 🚀 Quick Start

### Prerequisites

- [mise](https://mise.jdx.dev/) — auto-manages Python 3.12 + Node 22
- [Docker](https://www.docker.com/) — for the PostgreSQL 16 container
- [pnpm](https://pnpm.io/) — Node package manager
- An LLM API key — at least one of OpenAI / Anthropic / Google

### Local development

```bash
# 1. Install runtimes
mise install                          # Python 3.12 + Node 22

# 2. Start PostgreSQL
docker compose up postgres -d         # localhost:5432, moldy:moldy/moldy

# 3. Backend
cd backend
cp .env.example .env                  # set OPENAI_API_KEY, etc.
uv sync                               # install dependencies
uv run alembic upgrade head           # run migrations (head: m31)
uv run uvicorn app.main:app --reload --port 8001
# → http://localhost:8001/docs (Swagger UI)

# 4. Frontend (new terminal)
cd frontend
pnpm install
pnpm dev
# → http://localhost:3000
```

The first run seeds default models (GPT-5.5, Claude Sonnet 4.6, Gemini, …),
system tools, and four agent templates.

### Run everything with Docker Compose

```bash
export OPENAI_API_KEY=sk-...
docker compose up -d                  # postgres + backend + frontend
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
pnpm build                            # production build
```

## 📸 Screenshots

> Coming soon. Screen wireframes are documented in `docs/PRD-screens.md`.

## ✨ Features

<details>
<summary><b>🤖 Agent system</b></summary>

- **deepagents engine** — `create_deep_agent` over a compiled LangGraph that
  manages the message tree, branches, and checkpoints
- **Conversational builder** — Meta-agent interviews requirements and proposes
  build options (`agent_runtime/creation_agent.py`)
- **Agent templates** — Pre-built agents you can spawn instantly
- **Sub-agents** — Multi-level delegation (an agent invokes another agent as a tool)
- **Middleware system** — 22 middlewares across context engineering, planning,
  safety, reliability, and provider-specific categories
- **Model fallback chain** — Up to 5 fallback models if the primary call fails

</details>

<details>
<summary><b>💬 Chat + branching</b></summary>

- **SSE streaming** — Token-level live output with tool-call visualization
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

- **Built-in tool catalog** — DuckDuckGo, web scraper, current time, Naver
  search (5), Google CSE (3), Gmail (2), Calendar (3), Google Chat webhook
- **MCP integration** — Register stdio + HTTP servers via `langchain-mcp-adapters`,
  with import / export and health-check polling
- **Skill system** — `SKILL.md` (YAML frontmatter) plus auxiliary files; inline
  multi-file editor; create skills from scratch, upload, or import
- **Custom tools** — Define tool parameters with Pydantic schemas

</details>

<details>
<summary><b>🔐 Credentials · model management</b></summary>

- **Cipher V2 encryption** — Fernet + HKDF-SHA256 single-blob Base64
- **Vault integration** — `hvac`-based external secrets
- **System / user split** — Operator-managed credentials vs. per-user keys
- **Model discovery** — Probe LLM APIs through a credential to auto-pull the
  available model list, pricing, and context window
- **Model health checks** — Periodic probes monitor reachability
- **Benchmark rankings** — Surface LMArena, LiveBench, AAIndex scores

</details>

<details>
<summary><b>⏰ Triggers · usage · observability</b></summary>

- **Schedule triggers** — APScheduler-backed cron / interval, per-agent input
  message, Google Chat webhook notifications
- **Token usage tracking** — Per-agent / per-model / daily token + estimated cost
- **Daily spend** — Roll-ups by user / agent / model
- **LangSmith tracing** — Execution traces forwarded automatically

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
│    ├ creation_agent (meta builder)                              │
│    ├ executor (create_deep_agent + astream)                     │
│    ├ streaming (LangGraph events → SSE chunks via orjson)       │
│    ├ tool_factory (prebuilt + MCP + custom)                     │
│    ├ model_factory (per-provider LLM)                           │
│    └ trigger_executor (schedule → message)                      │
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
- **Model** (`app/models/`) — SQLAlchemy ORM, 31 tables as of m31

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
│   │   ├── dependencies.py      # get_db, get_current_user (mock)
│   │   ├── scheduler.py         # APScheduler singleton
│   │   ├── models/              # SQLAlchemy ORM
│   │   ├── schemas/             # Pydantic schemas
│   │   ├── routers/             # HTTP routers
│   │   ├── services/            # business logic
│   │   ├── credentials/         # Cipher V2 + domain
│   │   ├── agent_runtime/       # AI execution engine
│   │   └── seed/                # seed data
│   ├── alembic/versions/        # migrations (up to m31)
│   └── tests/                   # pytest (709 passing)
├── frontend/
│   └── src/
│       ├── app/                 # Next.js App Router (17 routes)
│       ├── components/          # UI components
│       └── lib/                 # api, hooks, stores, sse, types
├── docs/
│   ├── PRD.md                   # product requirements
│   ├── PRD-screens.md           # screen wireframes
│   ├── ARCHITECTURE.md          # system architecture
│   ├── design-docs/             # ADRs (design decisions)
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
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | yes (≥1) | LLM calls |
| `ENCRYPTION_KEY` | yes | Fernet key for at-rest API key encryption |
| `LANGSMITH_API_KEY` | optional | LangSmith tracing |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | optional | Naver search tools |
| `GOOGLE_API_KEY` / `GOOGLE_CSE_ID` | optional | Google CSE tools |
| Google OAuth2 token | optional | Gmail / Calendar tools (`scripts/google_oauth_setup.py`) |

Per-tool key setup is documented in [`docs/tool-setup-guide.md`](docs/tool-setup-guide.md).

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
