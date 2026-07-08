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
| 런타임 버전 | Python 3.12 (uv 자동 설치), Node 22 (`.node-version`) | |

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
│   ├── alembic/                 # DB 마이그레이션 (M1 ~ M63)
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
└── .node-version                # Node 22 핀 (nvm/fnm/asdf 호환; Python은 uv가 관리)
```

---

## 로컬 개발 환경 세팅

### 사전 요구사항

- [uv](https://docs.astral.sh/uv/) 설치 (Python 3.12 자동 프로비저닝 + backend 의존성)
- Node.js 22 + [pnpm](https://pnpm.io/) (프론트엔드; `.node-version`에 `22` 핀)
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

### 1. 런타임 준비

- Python 3.12 — `uv sync`(3단계) 시 자동 다운로드되므로 별도 설치 불필요
- Node 22 — 시스템 패키지·nvm·fnm 등으로 설치 (`.node-version`에 `22` 핀)

### 2. DB 실행

```bash
docker-compose up -d postgres
# PostgreSQL: localhost:5432, user=moldy, pass=moldy, db=moldy
```

### 3. Backend

```bash
cd backend
cp .env.example .env  # API 키 + ENCRYPTION_KEYS / JWT_SECRET 설정
uv sync               # 의존성 설치 (.venv 자동 생성)
uv run alembic upgrade head   # DB 마이그레이션 (M63까지)
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

빠른 전체 테스트가 필요하면 기본 설정을 바꾸지 않고 pytest-xdist를 임시로
사용한다. 저사양/CI에서는 `-n 4`부터 시작하고, 고사양 로컬에서는 `-n 8`까지
올려도 된다. `-n auto`는 worker가 과하게 늘어 오히려 느릴 수 있다.

```bash
cd backend
uv run --with pytest-xdist pytest -q -n 4
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
- checkpointer의 psycopg `AsyncConnectionPool`은 `CHECKPOINTER_POOL_MIN_SIZE`
  / `CHECKPOINTER_POOL_MAX_SIZE`로 조정된다(기본 1/10). 슬로우 스트리밍 런이나
  평가 런이 동시에 많이 돌면 백엔드 전체가 직렬화되어 무관한 요청까지 timeout
  날 수 있다. `--repeat-each` 스트레스 실패는 이 공유 풀/DB 부하가 원인일 수
  있으니 origin/main 대조 실행으로 분리 판단할 것.
  (UI 단언 타임아웃 영향은 `frontend/AGENTS.md` 참고.)
- 백엔드를 직접 띄우고 `reuseExistingServer`로 재사용할 때는 playwright의
  webServer 커맨드가 자동 주입하던 플래그가 빠진다. **`E2E_SCRIPTED_MODEL_ENABLED=true`**
  (키리스 scripted 모델)와 **`E2E_SEED_USER_ENABLED=true`**(seeded super_user —
  operator 전용 화면(system LLM/credentials, audit 등) 테스트에 필수; global-setup의
  register fallback은 일반 유저만 만든다)를 직접 켜야 한다.

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
- HiTL 기본 인터럽트: `execute_in_skill`처럼 위험 메타데이터가 있는 도구는 별도 미들웨어 설정 없이 **기본 `interrupt_on` 정책**이 붙는다 (`runtime_component_builder._default_interrupt_on_from_tools`). 즉 skill을 붙인 에이전트는 도구 실행 전 승인 카드에서 멈춘다 — HiTL을 켜려고 `middleware_configs`를 직접 짤 필요 없다 (명시 `human_in_the_loop` 설정은 정책 override일 뿐). 트리거 모드에서만 HiTL이 꺼진다.
- Generated file 규칙: user-visible 파일은 `/conversations/<thread_id>/...` 아래에 쓰게 유도하고 M59 `conversation_artifacts`로 인덱싱한다.
- 첨부 연결: 채팅 첨부(`POST /api/uploads`)는 전송 시 `chat_service.link_attachments_to_conversation`로 **대화에만** 연결되고 `message_attachments.message_id`는 null로 남는다. messages API는 message_id가 있는 첨부만 hydrate하므로 **첨부는 메시지 응답에 echo되지 않는다** — 검증/렌더는 업로드 행 자체(또는 conversation 단위)로 다뤄야 한다.

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

마이그레이션: `backend/alembic/versions/` (Alembic). 최신 head는 M63 (`chat_navigator_indexes`).

`is_system` 플래그가 있는 테이블 공통 제약: `CHECK ((is_system = false) OR (user_id IS NULL))`. 시스템 리소스는 user_id가 반드시 NULL.

---

## 환경 변수

`backend/.env.example` 참조. 최소 동작에 필요한 키:

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL async URL |
| `DATABASE_URL_SYNC` | O | PostgreSQL sync URL — LangGraph checkpointer 전용. `DATABASE_URL`에서 파생되지 않으니 DB 변경 시 둘 다 설정 |
| `ENCRYPTION_KEYS` | O | Cipher V2 마스터 키 (HKDF info=`moldy-encryption-v1`). 콤마 구분 64-char hex, 첫 번째가 활성 키. 복수 키 회전 지원 |
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
- **Trace/로그 redaction은 키 이름 substring 매칭에 의존하지 말 것.** substring 매칭은 over-redaction(`session_id`/`token_count`/`possession`/`my_secretary`가 모두 가려짐)과 leak(키 없는 `Bearer ...`, `{name,value}` 헤더 직렬화, `moldy_at=...` 비표준 토큰, URL 임베디드 credential 통과)을 동시에 유발한다. 단어 경계 패턴 + 헤더 키 정확 매칭으로 좁히고, 가능하면 **실제 주입된 credential 값 목록 기반 마스킹**을 보조 레이어로 둔다. usage 메트릭(`*_tokens`, `token_count`)은 명시적으로 safe 처리. 단, 단어 경계 매칭은 camelCase 키(`sessionToken`/`xApiKey`)를 놓치므로 **키 정규화(camelCase→snake)** 후 매칭한다. 값 매칭 정규식은 **와일드카드 겹침(`[A-Za-z0-9_-]*FRAG[A-Za-z0-9_-]*`)으로 ReDoS**(catastrophic backtracking; 영속화·SSE 스트리밍 hot path에서 공격자 제어 blob에 도달)를 유발하니, 키를 길이 상한(`{1,64}`) 단일 식별자 클래스로 잡고 sensitive 판정은 콜백에 위임하며, **타이밍 회귀 테스트(긴 식별자 blob이 1초 이내)**로 방어한다.
- **값 기반 마스킹은 dict 값뿐 아니라 dict 키에도 적용하고, 시크릿 수집은 `{name,value}` 헤더 shape를 인식할 것.** ① tool/API 응답이 주입된 시크릿을 객체 **키**로 echo하면 값만 재귀하는 마스커는 키에 평문을 남긴다 — Mapping 재귀에서 str 키에도 exact-substring 치환(`str.replace`, ReDoS 무관)을 적용한다. ② 시크릿 수집기가 리스트의 모든 str leaf를 적재하면 `[{"name":"Authorization","value":"Bearer..."}]`에서 헤더 **이름**(`Authorization`)까지 시크릿으로 수집돼 정상 산문이 over-redaction된다 — `{name|key|header, value}` shape는 `value`만 적재한다.
- **읽기/폴링 엔드포인트의 시크릿 수집은 런 준비(run-prep) 전체를 재실행하지 말 것.** `GET /messages`·`GET /threads/{id}/state`는 활성 대화당 반복 폴링되는데, `resolve_agent_context`(재귀 subagent 조립 + 런 identity 해석)를 그대로 쓰면 폴마다 N+ eager load·전체 credential 복호화가 돌아 폴 빈도에 비례해 비용이 폭증한다. **경량 agent-only 수집**(단일 `select(Agent)` + `build_tools_config`)을 read/poll·state·messages 경로가 **공유**한다(`chat_service.collect_conversation_secret_values`). 단, 시크릿 set 자체에 상한을 두어 **drop하지는 말 것** — 누락은 under-masking(누출)이라 O(secrets) 비용보다 위험하다.
- **기억(memory) 내용을 싣는 custom 이벤트를 새로 만들면 반드시 `protocol_redaction._redact_custom_event`의 매처에 등록할 것.** 영속 경로(`protocol_persistence`)는 `redact_memory=True` 기본값으로 memory 이벤트의 content를 `<redacted>` 마스킹하지만 **이름 기반 매처라 새 이벤트는 자동으로 걸리지 않는다** — 등록을 빼먹으면 사용자 기억이 message_events/공유 스냅샷에 평문으로 영속된다(W2-3 `moldy.memory_recalled`에서 발견). 프론트는 리로드 시 brief id로 소유자 전용 메모리 API를 재조회해 내용을 복원한다(memory-tool-ui의 proposal 재조회와 동일 계약).
- **HITL edit resume에서 `edited_action.name`은 백엔드가 pending action의 positional index로 채운다** (`conversation_agent_protocol_resume_redaction.py`). 프론트가 보낸 name은 advisory이며 무시된다 — 사용자가 승인 카드에서 본 슬롯의 도구명으로 실행되어 **도구 치환(tool substitution)을 막는다**. 프론트는 name을 생략할 수 있으나(백엔드가 채움), 매칭 raw action이 없어 name을 확정하지 못하면 **fail-closed**(그냥 통과시키면 langchain이 `edited_action["name"]`를 하드 subscript로 읽어 KeyError로 resume 크래시). 완화된 early-return 게이트는 **모든 edit**이 복원 경로를 타게 해 name-fill이 항상 수행되게 한다. redacted 시크릿 복원은 `value == "<redacted>"` **정확 일치**만 하므로, 시크릿은 필드 값 전체를 `<redacted>`로 잠가 정확-일치 경로로 수렴시킨다(값 내부 부분 마스킹은 복원 못 함).
- **히든 리소스(`agents.runtime_profile != 'standard'`) 필터는 "목록/집계 표면"만이 아니라 모든 에이전트 resolution 경로에 적용할 것** — 이름 기반 해석(블루프린트 `Agent.name.in_`), 트리거 생성(`_ensure_agent_fixed_for_trigger`), 어시스턴트 쓰기 도구(add_subagent)처럼 UUID/이름으로 직접 도달하는 형제 경로가 필터를 빼먹으면 히든 불변식이 부분 붕괴한다(스킬 빌더 리뷰에서 트리거 경로 발견 — 트리거는 빌더 분기·System LLM 재해석도 우회함). 새 runtime_profile 소비 경로를 추가하면 전수 grep로 형제 경로를 함께 닫는다.
- **enum 상태를 선언하면 그 상태로 전이하는 경로가 실제로 wire되어 있는지 확인할 것.** `skill_builder_sessions.abandoned`는 GC 삭제 대상으로 선언됐지만 전이 경로가 전무해 이탈 세션 워크스페이스가 영구 누수했다 — 상태 기계에 노드를 추가할 때 진입 간선 없는 노드는 죽은 GC 규칙이 된다.

### Frontend

- TypeScript strict mode
- React 19 Server Components 우선, `'use client'` 최소화
- UI: shadcn/ui 컴포넌트 우선 사용
- any 금지 → unknown + 타입 가드
- barrel export(index.ts) 지양
- **공유 transport/인터페이스(예: `MoldyAgentServerAdapter`)에 메서드를 추가하면, 그 인터페이스를 `vi.mock`으로 흉내 내는 모든 테스트 mock을 함께 갱신할 것.** 같은 훅을 검증하는 테스트들이 각자 mock을 중복 정의하면 누락 시 일괄 회귀(`X is not a function`)가 난다 → 공유 `createMockTransport()` 헬퍼로 추출하고, 머지 전 `pnpm vitest run` **전체**(개별 파일 아님)가 그린인지 확인한다.
- **v3 HITL 인터럽트는 원본 모델 도구호출 pill과 승인 카드를 이중 렌더한다.** `appendInterruptToolCallMessages`가 승인 카드를 별도 메시지로 추가하고 원본 `execute_in_skill` 등은 스트림에 남아, 승인/거부와 모순되는 상태(빨간 ✗ "완료" 등)의 중복 pill이 생긴다 → 승인 카드가 대표하는 raw tool call은 `stripInterruptedRawToolCalls`로 숨긴다(고아 ToolMessage 포함, active+resolved 매칭, `ask_user` 제외, 리로드 시 no-op). args 매칭은 **order-insensitive(정렬) stringify**로 — 인터럽트 이벤트와 메시지 스트림은 직렬화 경로가 달라 키 순서가 갈릴 수 있다. 빈 메시지 판정은 **블록배열 콘텐츠에서 `tool_use`만 있는 메시지를 text-less로 취급**해야 한다(그냥 `length>0`이면 실 Anthropic 콘텐츠에서 빈 버블이 남음).
- **묶음/compact 카드는 시간 기반 동작(카운트다운·auto-expire)을 컨테이너로 위임한다.** per-card 카운트다운을 숨긴 채 타이머만 살리면 결정 중 조용한 자동거부가 난다. **일괄 액션("모두 승인")은 진행 중(거부/수정 중) 항목을 덮어쓰지 않게** pristine-pending 상태의 카드만 콜백 등록 대상으로 한다(안 그러면 edit 중인 카드가 원본 args로 승인됨). 기존 makeAssistantToolUI 카드를 그룹 안에서 재사용할 땐 context(`MultiApprovalContext`)로 compact 여부·일괄 콜백을 전달하고 standalone 경로는 `grouped` 가드로 완전 보존한다.
- **`useAuiState` selector는 ① reference-stable 값(원시값/시그니처 문자열)을 반환하고 ② 부분 mock state에 방어적이어야 한다.** 새 배열/객체를 반환하면 useSyncExternalStore(Object.is)가 매번 불일치로 보고 무한 리렌더("Maximum update depth")가 나고(선례: tool-group-container의 출처 집계, W2-7 컴포저 히스토리 — `\u0000` join 시그니처로 안정화), 테스트 mock state에 해당 키가 없으면 크래시한다(`collectUserHistory`는 `Array.isArray` 가드로 방어).
- **도구 결과에서 온 URL을 `<a href>`/`<img src>`로 렌더할 때는 스킴을 검증할 것.** MCP/외부 API 결과는 신뢰 경계 밖이라 `javascript:` 주입이 가능하다 — `search-results.ts`의 `sanitizeExternalUrl`(http/https만) / `sanitizeThumbnailUrl`(+로컬 상대경로, protocol-relative `//` 차단)을 재사용한다.
- **프론트 표시 redaction(`sensitive-display.ts isSensitiveDisplayKey`)도 키 substring 매칭이라 `authorization`/`credential`/`cookie`/`passwd`/camelCase를 놓친다.** 시크릿을 표시·편집하는 경로(승인 카드 등)는 이 함수만 믿지 말고 **실제 주입된 credential 값 목록 기반 마스킹을 병행**한다(백엔드 substring 함정과 동일 — 위 Backend redaction 규칙 참조).
- **tool-call UI(makeAssistantToolUI render)는 args를 스트리밍 부분 JSON으로 받는다** — 배열 필드가 문자열/객체 조각인 순간에도 렌더가 호출되므로 `Array.isArray` + item shape 가드 없이 `.map/.filter`를 부르면 실 LLM 경로에서 렌더 크래시(route 에러 바운더리로 채팅 전체 다운)가 난다. **scripted 모델은 완성 args만 방출해 E2E가 전부 그린인 채로 이 클래스를 놓친다**(M8-4 write_todos에서 발견) — 새 tool UI는 `partial-streaming-args.test.tsx`에 조각-args 회귀를 추가한다. enum성 필드(`status` 등)도 조각 문자열일 수 있어 lookup은 `?? 폴백`으로 닫는다.
- **기능/컴포넌트를 제거하면 고아 헬퍼·i18n 네임스페이스를 함께 스윕할 것.** TS/eslint/i18n 가드는 "정의됐지만 아무도 안 쓰는" 파일·키를 잡지 못한다(빌더 다이얼로그 제거 시 preview-insights/preview-model 423줄 + `skill.builderDialog` 키가 고아로 잔존). 삭제 대상의 importer를 역추적해 그 파일이 유일한 소비자였던 모듈·키까지 지운다.

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

- **백엔드**: M63까지 마이그레이션 적용. 멀티유저 인증, marketplace skill publish/install, System LLM settings, schedule productization, Agent API, memory controls, audit events, generated artifacts, credential OAuth states, conversation runs, agent blueprints, chat navigator indexes, subagent runtime, executor split 반영.
- **프론트엔드**: 멀티유저 로그인/회원가입 UI, MCP 서버 관리, Skill/Credential/Marketplace 관리, 채팅 SSE 스트리밍, artifact preview/right rail/library, memory/settings, Agent API settings, 트리거 스케줄링, Builder 마법사.
- **다음 단계**: MCP/Agent marketplace 확장, artifact/share E2E 강화, long-running scheduler/worktree 운영 안정화.
- 자세한 태스크 현황은 `TASKS.md` 참조
- 기능 명세는 `docs/PRD.md` + `docs/PRD-screens.md` 참조
