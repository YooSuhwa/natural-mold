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

#### worktree E2E seed / env 동기화

`backend/.env.example`에 새 변수가 추가되어도 이미 존재하는 main checkout의
`backend/.env`에는 자동 반영되지 않는다. worktree는 main `backend/.env`를
symlink로 공유하므로, 새 worktree에서 Playwright E2E를 바로 돌리려면 main
`backend/.env`에 아래 값이 있는지 먼저 확인한다:

```dotenv
E2E_SEED_USER_ENABLED=true
E2E_USER_EMAIL=playwright-e2e@moldy.dev
E2E_USER_PASSWORD=correct horse battery staple 42
E2E_USER_NAME=E2E User
```

dev 환경에서 backend가 시작되면 `seed_e2e_user`가 위 계정을 DB에 super_user로
생성하거나 갱신한다. `APP_ENV=production`에서는 `E2E_SEED_USER_ENABLED=true`여도
항상 스킵된다. frontend E2E는 `frontend/.env.local`의 `E2E_USER_*` 값을
우선 사용하고, 호환을 위해 기존 `E2E_EMAIL` / `E2E_PASSWORD`도 fallback으로
읽는다.

#### E2E 캡처 / 이미지 산출물 규칙

Codex 내장 브라우저로 E2E를 요청받으면 실제 UI 조작과 검증은 내장 브라우저로
수행한다. 다만 내장 브라우저의 screenshot API가 `Page.captureScreenshot`
timeout, 빈 이미지, 깨진 이미지로 실패할 수 있다. 이 경우 검증 자체는 내장
브라우저에서 계속하고, 최종 공유용 이미지 파일만 같은 local dev server와 같은
E2E 계정으로 Playwright/Chrome 캡처 fallback을 사용한다. fallback을 사용했다면
최종 보고에 명시한다.

E2E 중 생성한 screenshot, video, trace, raw capture는 repo root에 흩뿌리지 말고
항상 아래 경로에 모은다:

```text
output/e2e-captures/<YYYYMMDD>-<feature>/
```

`output/`은 `.gitignore`에 포함되어 있으므로 이 산출물은 커밋하지 않는다. 과거처럼
`memory-e2e-*.png`, `*.raw` 같은 임시 파일을 repo root에 남기지 않는다. 사용자에게
이미지를 전달하기 전에는 `file output/e2e-captures/.../*.png`로 실제 PNG 여부와
해상도를 확인하고, `view_image`로 직접 열어 텍스트/카드가 잘리지 않거나 깨지지
않았는지 확인한다. 보안/secret 검증 화면을 캡처할 때는 실제 secret을 쓰지 않고
명확한 더미 값을 사용하며, 불필요한 더미 secret 문자열이 최종 이미지에 보이면
다시 캡처한다.

Tavily/Deep Research 구현 작업을 이어갈 때도 같은 원칙을 따른다.
`TAVILY_API_KEY`는 per-user credential이 아니라 backend hosted key로 main
`backend/.env`에 둔다. 상세 계획은
`docs/superpowers/plans/2026-05-31-deep-research-tavily.md`를 기준으로 한다.

#### worktree dev 서버 포트/CORS 규칙

워크트리에서 backend/frontend dev 서버를 띄울 때는 **frontend port,
backend port, CORS origin, `NEXT_PUBLIC_API_BASE_URL`을 한 세트로 맞춰야
한다.** 이 규칙을 지키지 않으면 브라우저에서 CORS, HttpOnly cookie, CSRF,
API base URL이 서로 어긋나 로그인/요청/DB 연결이 깨진 것처럼 보인다.

기본 권장값은 한 번에 하나의 worktree만 실행하고 고정 포트를 쓰는 것이다:

```bash
# backend
cd backend
uv run uvicorn app.main:app --reload --port 8001 --reload-dir app

# frontend
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

여러 worktree를 동시에 띄워야 하면 각 worktree마다 포트 쌍을 명시한다.
예를 들어 frontend `3010`, backend `8010`을 쓸 때:

```bash
# backend
cd backend
CORS_ALLOWED_ORIGINS=http://localhost:3010,http://127.0.0.1:3010 \
  uv run uvicorn app.main:app --reload --port 8010 --reload-dir app

# frontend
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010 pnpm dev -- --port 3010
```

에이전트는 worktree에서 dev 서버를 실행하거나 진단할 때 다음을 먼저 확인한다:

- `bash scripts/worktree-setup.sh`가 실행되어 `backend/.env`와 `backend/data`
  가 main checkout을 가리키는 symlink인지 확인
- main `backend/.env`에 E2E seed 값(`E2E_SEED_USER_ENABLED`, `E2E_USER_EMAIL`,
  `E2E_USER_PASSWORD`, `E2E_USER_NAME`)이 있는지 확인
- Deep Research/Tavily 작업이면 main `backend/.env`에 `TAVILY_API_KEY`가 있는지 확인
- frontend가 실제로 뜬 origin이 backend의 `CORS_ALLOWED_ORIGINS`에 포함되는지 확인
- frontend의 `NEXT_PUBLIC_API_BASE_URL`이 실제 backend port를 가리키는지 확인
- Next.js가 포트 충돌로 자동 선택한 임의 포트를 그대로 쓰지 말고 `pnpm dev -- --port <port>`로 고정
- 여러 backend를 같은 DB에 동시에 붙이면 APScheduler/trigger 작업이 중복 실행될 수 있으므로 장시간 동시 실행은 피하거나 scheduler 비활성화 옵션을 별도로 둔다

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
pnpm lint:design-system     # Moldy UI surface/radius/shadow/token 가드
```

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
| `E2E_SEED_USER_ENABLED`, `E2E_USER_EMAIL`, `E2E_USER_PASSWORD`, `E2E_USER_NAME` | X | 로컬 Playwright E2E용 더미 super_user seed. `APP_ENV=production`에서는 항상 skip |
| `TAVILY_API_KEY` | X | Tavily hosted search / Deep Research 계획용 backend key. per-user credential로 넣지 않는다 |
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
- PR 생성 시 GitHub connector가 `Resource not accessible by integration` / 403을
  반환하면 같은 connector 호출을 반복하지 않는다. 이 repo에서는 connector GitHub
  App 권한이 부족한 경우가 있으며, `git push`와 `gh pr create`는 사용자 `gh`
  인증 권한으로 정상 동작할 수 있다. 이 경우 `gh pr create --draft --base main
  --head <branch>` fallback을 사용하고 최종 보고에 fallback 사용 사실을 남긴다

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
- 사용자에게 보이는 모든 정적 텍스트는 `next-intl` 메시지(`frontend/messages/*.json`)를 통해 렌더링하고 TS/TSX에 직접 하드코딩하지 않는다
- Moldy의 기본 제품 언어는 한국어이므로 새 copy는 `frontend/messages/ko.json`에 먼저 자연스러운 한국어로 추가하고, 같은 key path를 `frontend/messages/en.json`에 적절한 영어로 반드시 함께 추가한다
- UI copy를 추가/수정한 뒤에는 `cd frontend && pnpm lint:i18n`을 실행한다. 영어/ASCII 정적 텍스트까지 점검해야 하는 경우 `pnpm lint:i18n:strict`가 있으나, 기존 Agent Prism/코드 조각 오탐이 남아 있으므로 실패 시 실제 사용자 노출 문구는 i18n으로 옮기고 가드는 좁게 조정한다
- any 금지 → unknown + 타입 가드
- barrel export(index.ts) 지양
- Moldy 디자인 시스템 가드: 새 UI 작업 후 `cd frontend && pnpm lint:design-system` 실행
- 제품 화면에서 `rounded-xl/2xl/3xl`, `shadow-sm/md/lg/xl/2xl`, `shadow-[...]`, raw hex utility(`bg-[#...]` 등), `text-[...]`, `outline-none`, `transition-all` 직접 사용 금지. `moldy-card`, `moldy-panel`, `moldy-popover`, `moldy-skeleton-card`, `moldy-status-*`, `moldy-muted-panel` 등 공용 class/token으로 이동
- 임의 `style={...}`는 동적 layout/library API일 때만 허용하고, `frontend/scripts/check-design-system.mjs` allowlist에 이유와 좁은 context regex를 함께 남긴다
- 현재 허용된 inline style 예외는 tree depth indentation, syntax highlighter theme, usage bar width, resource grid columns, Agent Prism trace/timeline layout, phase progress ratio뿐이다

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
