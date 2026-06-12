# Moldy — AI Agent Builder

노코드로 AI 에이전트를 만들고, 채팅하고, 스케줄링하는 웹 애플리케이션.
**ADR-016에 따라 멀티유저 인증 적용 완료** (JWT + super_user). 운영자(super_user)와 일반 사용자 권한이 분리되어 있다.

---

## 기술 스택

| 레이어 | 기술 | 버전 |
|--------|------|------|
| Frontend | Next.js (App Router) + React + TailwindCSS v4 + shadcn/ui | Next 16, React 19 |
| 상태관리 | TanStack Query (서버), Jotai (클라이언트) | |
| Backend | FastAPI + SQLAlchemy (async) + Alembic | FastAPI 0.115+, SA 2.0+ |
| AI Runtime | LangChain 1.x + LangGraph 1.x + **deepagents** + LangSmith | `create_deep_agent` 기반 |
| 인증 | JWT (HS256) + HttpOnly Cookie + CSRF double-submit | ADR-016 |
| 암호화 | Cipher V2 — HKDF-SHA256 + AES-256-GCM, multi-key rotation | ADR-009 |
| DB | PostgreSQL 16 (docker-compose) | |
| 스케줄러 | APScheduler 3.x | |
| 패키지 매니저 | uv (backend), pnpm (frontend) | |
| 런타임 버전 | Python 3.12, Node 22 (mise로 관리) | |

---

## 프로젝트 구조

```
natural-mold/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory + lifespan (시드, 스케줄러, checkpointer)
│   │   ├── config.py            # pydantic-settings (.env 기반)
│   │   ├── database.py          # async engine + session
│   │   ├── dependencies.py      # get_db, get_current_user, require_super_user, verify_csrf
│   │   ├── scheduler.py         # APScheduler 싱글턴 (MCP health polling, refresh token GC 등)
│   │   ├── models/              # SQLAlchemy ORM (user, agent, skill, mcp_server, credential, ...)
│   │   ├── schemas/             # Pydantic request/response 스키마
│   │   ├── routers/             # FastAPI 라우터 (agents, tools, skills, mcp, credentials, auth, ...)
│   │   ├── services/            # 비즈니스 로직 레이어
│   │   ├── auth/                # ADR-016 멀티유저 인증 (JWT, refresh token rotation, CSRF)
│   │   ├── security/            # Cipher V2 (HKDF-SHA256 + AES-256-GCM, multi-key rotation)
│   │   ├── credentials/         # 자격증명 시스템
│   │   │   ├── definitions/     # 22개 credential type 정의 (LLM, 검색, MCP, k-skill 계열 등)
│   │   │   ├── service.py       # CRUD + field_keys 캐시 (ADR-007)
│   │   │   ├── interpolation.py # {{$credentials.x}} 보간 (resolve_deep)
│   │   │   └── external_secrets/ # Vault/ENV resolver (feature flag 기반)
│   │   ├── skills/              # Skill 시스템 (text + .skill package)
│   │   │   ├── service.py       # CRUD + 파일시스템 (data/skills/<id>/)
│   │   │   ├── packager.py      # .skill ZIP extract (symlink/zip-slip/null-byte 방어)
│   │   │   ├── inspector.py     # SKILL.md frontmatter parse
│   │   │   ├── runtime.py       # build_skills_for_agent (AgentSkillLink → descriptor)
│   │   │   └── prompt.py        # build_skills_prompt (LLM에 skill 존재 알림)
│   │   ├── mcp/                 # MCP 통합
│   │   │   ├── client.py        # connect_and_list (transports: stdio/sse/streamable_http)
│   │   │   └── discovery.py     # tool 발견 + credential 보간 + last_seen_at upsert
│   │   ├── agent_runtime/       # AI 실행 엔진 (deepagents 기반)
│   │   │   ├── executor.py      # compatibility facade (runtime split exports)
│   │   │   ├── runtime_config.py # AgentConfig + RuntimeComponents
│   │   │   ├── runtime_component_builder.py # model/tools/skills/memory/subagents 조립
│   │   │   ├── agent_stream_runner.py # stream/invoke 실행 + hooks/Langfuse
│   │   │   ├── skill_executor.py # execute_in_skill subprocess runner
│   │   │   ├── mcp_tool_loader.py # MCP runtime tool loading
│   │   │   ├── model_factory.py # LLM 인스턴스 + GPT-5/Anthropic quirks (ADR-014)
│   │   │   ├── tool_factory.py  # builtin/registry/MCP 도구 빌더
│   │   │   ├── middleware_registry.py # 22개 미들웨어 카탈로그 (auto/explicit/provider 분리)
│   │   │   ├── streaming.py     # LangGraph → SSE (W3-out 부분 플러시)
│   │   │   ├── checkpointer.py  # AsyncPostgresSaver
│   │   │   ├── credential_resolution.py # LLM credential 3단계 우선순위 (ADR-013)
│   │   │   ├── builder_v3/      # 대화형 에이전트 생성 그래프
│   │   │   ├── assistant/       # Assistant panel agent/tools
│   │   │   ├── mcp_client.py    # MCP wrapper (app.mcp.client에 위임)
│   │   │   ├── trigger_executor.py # 스케줄 트리거 실행 (invoke 모드, HiTL 비활성)
│   │   │   ├── naver_tools.py   # 네이버 검색 API 도구
│   │   │   ├── google_tools.py  # Google Custom Search 도구
│   │   │   └── google_workspace_tools.py # Gmail, Calendar, Chat Webhook
│   │   └── seed/                # 시드 데이터 (모델, 템플릿, 시스템 도구, bootstrap_from_env)
│   ├── alembic/                 # DB 마이그레이션 (M1 ~ M59)
│   ├── tests/                   # pytest (aiosqlite in-memory)
│   ├── scripts/                 # 유틸리티 (migrate_mock_to_real_user, google_oauth_setup, ...)
│   ├── pyproject.toml
│   └── .env.example             # 환경변수 템플릿
│
├── frontend/                    # Next.js 16 + React 19 (App Router)
│   ├── src/
│   │   ├── app/                 # 라우트 (/, /agents, /tools, /skills, /mcp-servers, /credentials, /usage)
│   │   ├── components/          # ui (shadcn), layout, agent, chat, tool, shared
│   │   ├── lib/                 # api, hooks (TanStack Query), stores (Jotai), sse, types
│   │   └── hooks/
│   ├── package.json
│   └── AGENTS.md                # Next.js 16 주의사항
│
├── docs/
│   ├── PRD.md                   # 제품 요구사항 정의서
│   ├── PRD-screens.md           # 화면별 와이어프레임
│   ├── ARCHITECTURE.md          # 시스템 아키텍처
│   ├── design-docs/             # ADR + 설계 스펙 (adr-001 ~ adr-019, 멀티유저 UI spec 등)
│   ├── tool-setup-guide.md      # 프리빌트 도구 API 키 설정 가이드
│   └── marketplace-resources-prd.md # Agent/MCP/Skill 마켓플레이스 PRD + 구현 상태
│
├── docker-compose.yml           # PostgreSQL + Backend + Frontend
├── TASKS.md                     # 태스크 목록
└── .mise.toml                   # Python 3.12, Node 22
```

---

## 로컬 개발 환경 세팅

### 사전 요구사항

- [mise](https://mise.jdx.dev/) 설치 (Python, Node 버전 관리)
- Docker Desktop (PostgreSQL용)

### git worktree 에서 작업 시

`backend/.env` 는 `.gitignore` 라 worktree 마다 별도 파일이 필요하지만, ground
truth 는 main checkout 의 `backend/.env` 하나로 유지해야 한다 (같은 PG DB +
같은 `ENCRYPTION_KEYS` 라야 기존 credential 복호화 정상 + `JWT_SECRET` 공유로
세션도 share 됨). worktree 진입 후 1회만 실행:

```bash
bash scripts/worktree-setup.sh
```

스크립트는 멱등 — `backend/.env` 가 main 의 `.env` 로 symlink 되어 있는지
확인하고, 없으면 생성한다. 또한 `uvicorn --reload` 가 publish/install 시
`data/` 디렉토리 변경을 자동 reload 트리거하지 않도록 `--reload-dir app` 추가
권장 안내도 출력한다.

### 1. 런타임 설치

```bash
mise install          # Python 3.12 + Node 22
```

### 2. DB 실행

```bash
docker-compose up -d postgres
# PostgreSQL: localhost:5432, user=moldy, pass=moldy, db=moldy
```

### 3. Backend

```bash
cd backend
cp .env.example .env  # API 키 + ENCRYPTION_KEY 설정
uv sync               # 의존성 설치 (.venv 자동 생성)
uv run alembic upgrade head   # DB 마이그레이션 (M59까지)
uv run uvicorn app.main:app --reload --port 8001
# → http://localhost:8001/docs (Swagger UI)
# 시작 시 시드 데이터 자동 삽입 (모델, 템플릿, ENV → system credentials bootstrap)
```

### 4. Frontend

```bash
cd frontend
pnpm install
pnpm dev
# → http://localhost:3000
# Backend URL: NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 (.env.local)
```

### 5. 테스트

```bash
cd backend
uv run pytest               # 전체 테스트 (aiosqlite in-memory, DB 불필요)
uv run pytest tests/test_agents.py  # 개별 파일
uv run ruff check .         # 린트
```

```bash
cd frontend
pnpm build                  # 타입 체크 + 빌드
pnpm lint                   # ESLint
```

#### E2E 포트/DB 격리 (throwaway 스택)

기본 포트(3000/8001/5432)를 다른 프로젝트가 점유 중이면 throwaway 스택으로 격리해 실행한다:

```bash
# 1) throwaway Postgres (예: 호스트 5433)
docker run -d --name moldy-e2e-pg -p 5433:5432 \
  -e POSTGRES_DB=moldy -e POSTGRES_USER=moldy -e POSTGRES_PASSWORD=moldy postgres:16-alpine

# 2) 마이그레이션 — throwaway DB에만 직접 실행 (공유/main DB 금지)
cd backend && DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy' \
  uv run alembic upgrade head

# 3) E2E 실행 (playwright webServer가 backend+frontend 자체 기동)
cd frontend && \
E2E_FRONTEND_PORT=3100 E2E_BACKEND_PORT=8101 \
DATABASE_URL='postgresql+asyncpg://moldy:moldy@localhost:5433/moldy' \
DATABASE_URL_SYNC='postgresql://moldy:moldy@localhost:5433/moldy' \
RATE_LIMIT_ENABLED=false E2E_TEST_HELPERS_ENABLED=true \
pnpm exec playwright test e2e/<spec>.spec.ts
```

주의:

- `DATABASE_URL_SYNC`는 `DATABASE_URL`에서 파생되지 않는 **별도 설정**이다
  (`backend/app/config.py`). LangGraph checkpointer가 이 값을 쓰므로 **둘 다**
  오버라이드해야 한다. 하나만 바꾸면 checkpointer가 기존 DB를 바라보다
  PoolTimeout으로 백엔드 기동에 실패한다.
- checkpointer의 psycopg `AsyncConnectionPool`은 min/max 미지정으로 **고정
  4 커넥션**이다. 슬로우 스트리밍 런 4개 이상이 동시에 돌면 백엔드 전체가
  직렬화되어 무관한 요청까지 timeout 난다. `--repeat-each` 스트레스 실패는
  이 인프라 한계가 원인일 수 있으니 origin/main 대조 실행으로 분리 판단할 것.

---

## 아키텍처 패턴

### Backend: Router → Service → Model

```
Router (routers/)      → HTTP 엔드포인트, 요청/응답 변환, 권한 가드
Service (services/)    → 비즈니스 로직, DB 쿼리
Model (models/)        → SQLAlchemy ORM
Schema (schemas/)      → Pydantic 입출력 스키마
```

- 모든 DB 접근은 async (`AsyncSession`)
- 의존성 주입: `Depends(get_db)`, `Depends(get_current_user)`, `Depends(require_super_user)`, `Depends(verify_csrf)`
- 인증: JWT(HS256) + HttpOnly Cookie(`moldy_at`, `moldy_rt`, `moldy_csrf`). `Authorization: Bearer` 헤더도 지원
- 권한 모델: `is_super_user` boolean (RBAC 확장은 후속). 시스템 리소스(`is_system=True`, `user_id IS NULL`) 관리는 super_user 전용

### Backend: AI Runtime (agent_runtime/)

```
executor.py                → compatibility facade (분리 모듈 re-export)
runtime_config.py          → AgentConfig + RuntimeComponents
runtime_component_builder  → model/tools/skills/memory/subagents + create_deep_agent 준비
agent_stream_runner        → stream/invoke 실행, hooks, Langfuse context
skill_executor             → execute_in_skill subprocess runner
mcp_tool_loader            → MCP runtime tool loading
model_factory              → provider별 LLM 인스턴스 (OpenAI/Anthropic/Google/OpenRouter/openai_compatible)
tool_factory               → builtin/registry 도구 빌더 + shared HTTP client
middleware_registry        → 22개 미들웨어 (deepagents auto-injected vs 명시 인스턴스)
streaming                  → LangGraph 이벤트 → SSE + trace/artifact/usage capture
checkpointer               → AsyncPostgresSaver (thread_id별 상태 영속화)
credential_resolution      → LLM credential 우선순위 (직접 binding > 모델 기본 > provider 단일 매칭)
trigger_executor           → 스케줄 트리거 (invoke 모드, ask_user/HiTL 비활성)
```

- 도구 타입: `builtin:*` (web_search, web_scraper, current_datetime), `registry`(Tool 모델의 definition_key 기반), `mcp`(AgentMcpToolLink)
- Skill 시스템: 선택된 skill만 `/runtime/<thread_id>/.../skills/` 가상 경로에 노출한다. LLM은 `read_file`로 `SKILL.md`를 먼저 읽고 지시를 따른다.
- Skill subprocess 실행: **`execute_in_skill` 도구**는 `skill_executor.py`에 있으며 Python 스크립트 allowlist, timeout, output dir, credential env injection, redaction 계약을 사용한다.
- Generated file 규칙: user-visible 파일은 `/conversations/<thread_id>/...` 아래에 쓰게 유도하고 M59 `conversation_artifacts`로 인덱싱한다.

### Frontend: API Client → TanStack Query → Component

```
lib/api/        → fetch 래퍼 (도메인별 파일)
lib/hooks/      → useMutation, useQuery 래핑
lib/stores/     → Jotai atoms (채팅 메시지, 사이드바 상태 등)
lib/sse/        → EventSource 기반 SSE 스트리밍
lib/types/      → Backend 스키마와 1:1 대응하는 TS 타입
```

- 서버 상태(API 데이터) → TanStack Query
- 클라이언트 상태(UI 로컬) → Jotai
- SSE 스트리밍: 채팅 응답을 실시간으로 수신

### Next.js 16 주의사항

> **이 프로젝트는 Next.js 16을 사용한다.** 기존 Next.js와 API/컨벤션이 다를 수 있다.
> 코드 작성 전 반드시 `frontend/node_modules/next/dist/docs/`의 가이드를 확인할 것.

---

## DB 스키마 (핵심 테이블)

| 테이블 | 설명 |
|--------|------|
| `users` | 사용자 (hashed_password, is_active, is_super_user, login tracking, lockout) |
| `refresh_tokens` | JWT refresh token whitelist (rotation + replay detection) |
| `agents` | AI 에이전트 (system_prompt, model_id, llm_credential_id, model_fallback_list) |
| `agent_tools` | 에이전트-도구 연결 |
| `agent_skills` | 에이전트-skill 연결 + `config`(credential binding override) |
| `agent_mcp_tools` | 에이전트-MCP 도구 연결 |
| `agent_subagents` | parent agent → child agent delegation |
| `agent_triggers`, `agent_trigger_runs` | 스케줄 트리거와 실행 이력 |
| `models` | LLM 모델 정의 (provider, model_id, default_credential_id) |
| `tools` | 도구 (definition_key, parameters, credential_id, `is_system`) |
| `skills` | Skill (kind=text|package, storage_path, content_hash, version, package_metadata) |
| `mcp_servers` | MCP 서버 (transport, url/command, env_vars, headers, credential_id, `is_system`, health_status) |
| `mcp_tools` | MCP 서버에서 발견된 도구 (input_schema, enabled, last_seen_at) |
| `credentials` | 자격증명 (definition_key, data_encrypted, key_id, field_keys, `is_system`) |
| `conversations` | 대화 세션 + active branch checkpoint |
| `message_events`, `message_event_chunks` | SSE 이벤트 스트림, streaming resume, trace correlation |
| `message_attachments`, `message_feedback` | 첨부/피드백 |
| `conversation_artifacts`, `artifact_versions` | 생성 파일 artifact와 버전 (M59) |
| `share_links` | 대화 공유 링크 (M30/M31) |
| `token_usages` | 토큰 사용량 추적 |
| `templates` | 에이전트 템플릿 |
| `builder_sessions` | 대화형 에이전트 생성 세션 (구 agent_creation_sessions) |
| `marketplace_*` | marketplace item/version/ACL/installation/publication/binding |
| `memory_*` | user/agent memory settings, records, proposals |
| `agent_deployments`, `agent_api_*` | 외부 Agent API deployment/key/thread/run |
| `audit_events`, `daily_spend_*`, `health_check_history` | 감사, 비용 집계, health history |
| `system_llm_settings` | Builder/Assistant/Image role별 system model 설정 |

마이그레이션: `backend/alembic/versions/` (Alembic). 최신은 M59 (`conversation_artifacts`).

`is_system` 플래그가 있는 테이블 공통 제약: `CHECK ((is_system = false) OR (user_id IS NULL))`. 시스템 리소스는 user_id가 반드시 NULL.

---

## 환경 변수

`backend/.env.example` 참조. 최소 동작에 필요한 키:

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL async URL |
| `ENCRYPTION_KEY` | O | Cipher V2 마스터 키 (HKDF info=`moldy-encryption-v1`). 복수 키 회전 지원 |
| `JWT_SECRET` | O | JWT HS256 서명 키 (ADR-016) |
| LLM 키 (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` 등) | X (선택) | UI Credentials에서 등록 권장 (ADR-013). ENV에 있으면 dev에서 system credential로 bootstrap, production은 skip |
| 나머지 (Naver, Google 등) | X | 해당 도구 사용 시에만 필요 |

ENV에서 자동으로 생성되는 `is_system=True` credentials는 production 환경에서는 자동 생성을 건너뛰고, super_user가 직접 관리한다.

---

## 자격증명 시스템 (ADR-007/009/013)

- **Cipher V2** (`app/security/cipher.py`): HKDF-SHA256 → AES-256-GCM. Blob 구조 `[ver 1B | salt 32B | tag 16B | ciphertext]`, Base64 인코딩
- **Multi-key rotation**: `credentials.key_id`로 활성 키 식별, 복호화는 모든 candidate 키 시도. APScheduler가 주1회 `rotate_credentials_to_active_key` 실행
- **field_keys 캐시** (ADR-007): list API에서 N+1 복호화 회피. 키 이름만 JSON 컬럼에 저장
- **definition_key 등록**: `app/credentials/definitions/__init__.py`에서 22개 등록 (LLM, Google/Naver, HTTP, MCP secret/OAuth2, SRT/KTX/Forest Trip/KIPRIS/DART/ODsay/Coupang/K-Skill Proxy 등)
- **LLM credential 우선순위** (ADR-013): ENV fallback → system credentials → user credentials
- **외부 secret resolver** (`external_secrets/`): `__external__:<provider>:<ref>` 마커로 Vault/ENV 동적 해석 (feature flag)
- **시스템 credential 분리** (M36, M39): `is_system=True && user_id IS NULL`. 일반 사용자는 `/api/credentials`로 보이지 않고 super_user 전용 system credential 화면/API로 관리

---

## 빌트인 도구 시스템

시스템 도구(`is_system=True`)는 서버 시작 시 자동 시드된다.

| 도구 | 타입 | 필요 키 |
|------|------|---------|
| Web Search (DuckDuckGo) | builtin:web_search | 없음 |
| Web Scraper | builtin:web_scraper | 없음 |
| Current DateTime | builtin:current_datetime | 없음 (Seoul TZ) |
| Naver 검색 (5종) | registry | naver_search credential |
| Google 검색 (3종) | registry | google_search credential |
| Google Chat Webhook | registry | URL credential |
| Gmail (2종) | registry | google_workspace_oauth2 |
| Calendar (3종) | registry | google_workspace_oauth2 |

서버 키가 설정되지 않은 도구는 에이전트별 credential을 개별 제공할 수 있다. 도구 카탈로그(`ToolDefinition`)는 **메모리 기반 registry**(`app/tools/registry.py`)에서 운영자가 코드로 정의한다.

---

## 개발 컨벤션

### Git

- 커밋 메시지: `<type>(<scope>): <subject>` (영문, 명령형)
- 브랜치: `feature/{task-name}`, `fix/{issue}`, `refactor/{target}`
- main 직접 커밋 금지, feature 브랜치에서 작업 후 머지

### Backend

- 타입 힌트 필수 (함수 시그니처, 반환 타입)
- async/await 패턴, `select()` 구문
- 린터: ruff (line-length=100, target=py312)
- 테스트: pytest + aiosqlite in-memory (PostgreSQL 불필요)
- 새 테이블 추가 시 Alembic 마이그레이션 필수
- Ownership 검증: enumeration oracle 방지 — 없음(404)과 권한 없음(403) 응답을 외부로 동일하게 통일

### Frontend

- TypeScript strict mode
- React 19 Server Components 우선, `'use client'` 최소화
- UI: shadcn/ui 컴포넌트 우선 사용
- any 금지 → unknown + 타입 가드
- barrel export(index.ts) 지양

---

## ADR 인덱스

| ID | 제목 | 핵심 |
|----|------|------|
| ADR-001 | Deep Agent Engine | `create_deep_agent` 단일화, MCP는 `MultiServerMCPClient` |
| ADR-002 | Checkpointer | LangGraph `AsyncPostgresSaver` |
| ADR-003 | Skills Memory | skill 시스템 + memory 설계 |
| ADR-004 | M4 Cleanup | 마이그레이션 정리 |
| ADR-005 | Builder Assistant | 대화형 에이전트 생성 메타 에이전트 |
| ADR-006 | Assistant UI Runtime | UI 실행 정책 |
| ADR-007 | Credentials field_keys Cache | list API N+1 복호화 회피 |
| ADR-008 | Connection Entity | MCP/credential 연결 모델 |
| ADR-009 | Greenfield Credentials | Cipher V2, multi-key rotation |
| ADR-010 | UI Tokens / Dialog Shell | 디자인 토큰 |
| ADR-011 | SSE Stream Resume | 스트리밍 재개 |
| ADR-012 | HiTL Middleware Migration | deepagents 자동 주입 회피, 명시 인스턴스 추가 |
| ADR-013 | Service LLM Key from Credentials | ENV > system > user 우선순위 |
| ADR-014 | Chat Model Factory Strategy | GPT-5 / Anthropic quirk 분리 |
| ADR-016 | Multi-user Auth | JWT + HttpOnly + refresh rotation + super_user |
| ADR-017 | Marketplace Resources | Skill/MCP/Agent 공유 레이어, Phase 1 Skill |
| ADR-018 | Relative Storage Path | worktree 간 data 경로 안정화 |
| ADR-019 | System LLM Settings | 역할별 모델 선택 + base_url 주입 |

각 ADR 본문은 `docs/design-docs/`에 있다.

---

## 현재 상태 요약

- **백엔드**: M59(2026-06-06)까지 마이그레이션 적용. 멀티유저 인증, marketplace skill publish/install, System LLM settings, schedule productization, Agent API, memory controls, audit events, generated artifacts, subagent runtime, executor split 반영.
- **프론트엔드**: 멀티유저 로그인/회원가입 UI, MCP 서버 관리, Skill/Credential/Marketplace 관리, 채팅 SSE 스트리밍, artifact preview/right rail/library, memory/settings, Agent API settings, 트리거 스케줄링, Builder 마법사.
- **다음 단계**: MCP/Agent marketplace 확장, artifact/share E2E 강화, long-running scheduler/worktree 운영 안정화.
- 자세한 태스크 현황은 `TASKS.md` 참조
- 기능 명세는 `docs/PRD.md` + `docs/PRD-screens.md` 참조
