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
│   │   │   ├── definitions/     # 14개 credential type 정의 (openai, anthropic, naver_search, ...)
│   │   │   ├── service.py       # CRUD + field_keys 캐시 (ADR-007)
│   │   │   ├── interpolation.py # {{$credentials.x}} 보간 (resolve_deep)
│   │   │   └── external_secrets.py # Vault/ENV resolver (feature flag 기반)
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
│   │   │   ├── executor.py      # create_deep_agent + FilesystemBackend + execute_in_skill
│   │   │   ├── model_factory.py # LLM 인스턴스 + GPT-5/Anthropic quirks (ADR-014)
│   │   │   ├── tool_factory.py  # builtin/registry/MCP 도구 빌더
│   │   │   ├── middleware_registry.py # 22개 미들웨어 카탈로그 (auto/explicit/provider 분리)
│   │   │   ├── streaming.py     # LangGraph → SSE (W3-out 부분 플러시)
│   │   │   ├── checkpointer.py  # AsyncPostgresSaver
│   │   │   ├── credential_resolution.py # LLM credential 3단계 우선순위 (ADR-013)
│   │   │   ├── creation_agent.py # 대화형 에이전트 생성 메타 에이전트
│   │   │   ├── mcp_client.py    # MCP wrapper (app.mcp.client에 위임)
│   │   │   ├── trigger_executor.py # 스케줄 트리거 실행 (invoke 모드, HiTL 비활성)
│   │   │   ├── naver_tools.py   # 네이버 검색 API 도구
│   │   │   ├── google_tools.py  # Google Custom Search 도구
│   │   │   └── google_workspace_tools.py # Gmail, Calendar, Chat Webhook
│   │   └── seed/                # 시드 데이터 (모델, 템플릿, 시스템 도구, bootstrap_from_env)
│   ├── alembic/                 # DB 마이그레이션 (M1 ~ M39)
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
│   ├── design-docs/             # ADR + 설계 스펙 (adr-001 ~ adr-016, 멀티유저 UI spec 등)
│   ├── tool-setup-guide.md      # 프리빌트 도구 API 키 설정 가이드
│   └── marketplace-resources-prd.md # Agent/MCP/Skill 마켓플레이스 PRD (작성 중)
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
uv run alembic upgrade head   # DB 마이그레이션 (M39까지)
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
executor.py             → create_deep_agent + FilesystemBackend(virtual_mode=True)
model_factory           → provider별 LLM 인스턴스 (OpenAI/Anthropic/Google/OpenRouter/openai_compatible)
tool_factory            → builtin/registry/MCP 도구 (definition_key 분기)
middleware_registry     → 22개 미들웨어 (deepagents auto-injected vs 명시 인스턴스)
streaming               → LangGraph 이벤트 → SSE (32 events 또는 2초마다 부분 플러시)
checkpointer            → AsyncPostgresSaver (thread_id별 상태 영속화)
credential_resolution   → LLM credential 우선순위 (직접 binding > 모델 기본 > provider 단일 매칭)
trigger_executor        → 스케줄 트리거 (invoke 모드, ask_user/HiTL 비활성)
```

- 도구 타입: `builtin:*` (web_search, web_scraper, current_datetime), `registry`(Tool 모델의 definition_key 기반), `mcp`(AgentMcpToolLink)
- Skill 시스템: `["/skills/"]`를 `FilesystemBackend(root_dir=data/)` 위에 마운트 → LLM이 `read_file('/skills/<slug>/SKILL.md')`로 본문을 읽음
- Skill subprocess 실행: **`execute_in_skill` 도구** 이미 도입 (executor.py:113-195). Python 스크립트만 allowlist, 30초 타임아웃, `_DATA_DIR` 하위 경로 검증
- 현재 한계(마켓플레이스 PRD 대상): subprocess env에 credential 미주입, skill 마운트가 broad(`/skills/` 전체)

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
| `agent_skills` | 에이전트-skill 연결 (현재 config 필드 없음) |
| `agent_mcp_tools` | 에이전트-MCP 도구 연결 (M25 추가) |
| `agent_triggers` | 스케줄 트리거 (cron/interval) |
| `models` | LLM 모델 정의 (provider, model_id, default_credential_id) |
| `tools` | 도구 (definition_key, parameters, credential_id, `is_system`) |
| `skills` | Skill (kind=text|package, storage_path, content_hash, version, package_metadata) |
| `mcp_servers` | MCP 서버 (transport, url/command, env_vars, headers, credential_id, `is_system`, health_status) |
| `mcp_tools` | MCP 서버에서 발견된 도구 (input_schema, enabled, last_seen_at) |
| `credentials` | 자격증명 (definition_key, data_encrypted, key_id, field_keys, `is_system`) |
| `conversations` | 대화 세션 |
| `messages` | 대화 메시지 (message_events, attachments, linked_message_ids, branch 지원) |
| `message_events` | 대화 이벤트 스트림 (W3-out streaming status, M32/M34) |
| `share_links` | 대화 공유 링크 (M30/M31) |
| `token_usages` | 토큰 사용량 추적 |
| `templates` | 에이전트 템플릿 |
| `builder_sessions` | 대화형 에이전트 생성 세션 (구 agent_creation_sessions) |

마이그레이션: `backend/alembic/versions/` (Alembic). 최신은 M39 (system credential dedup).

`is_system` 플래그가 있는 테이블 공통 제약: `CHECK ((is_system = false) OR (user_id IS NULL))`. 시스템 리소스는 user_id가 반드시 NULL.

---

## 환경 변수

`backend/.env.example` 참조. 최소 동작에 필요한 키:

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL async URL |
| `ENCRYPTION_KEY` | O | Cipher V2 마스터 키 (HKDF info=`moldy-encryption-v1`). 복수 키 회전 지원 |
| `JWT_SECRET` | O | JWT HS256 서명 키 (ADR-016) |
| `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY` | O (하나 이상) | LLM 호출용. ENV→system credential 자동 bootstrap |
| 나머지 (Naver, Google 등) | X | 해당 도구 사용 시에만 필요 |

ENV에서 자동으로 생성되는 `is_system=True` credentials는 production 환경에서는 자동 생성을 건너뛰고, super_user가 직접 관리한다.

---

## 자격증명 시스템 (ADR-007/009/013)

- **Cipher V2** (`app/security/cipher.py`): HKDF-SHA256 → AES-256-GCM. Blob 구조 `[ver 1B | salt 32B | tag 16B | ciphertext]`, Base64 인코딩
- **Multi-key rotation**: `credentials.key_id`로 활성 키 식별, 복호화는 모든 candidate 키 시도. APScheduler가 주1회 `rotate_credentials_to_active_key` 실행
- **field_keys 캐시** (ADR-007): list API에서 N+1 복호화 회피. 키 이름만 JSON 컬럼에 저장
- **definition_key 등록**: `app/credentials/definitions/__init__.py`에서 14개 등록 (anthropic, openai, google_genai, azure_openai, openrouter, openai_compatible, google_search, naver_search, google_workspace_oauth2, http_bearer, http_basic, http_api_key, mcp_oauth2)
- **LLM credential 우선순위** (ADR-013): ENV fallback → system credentials → user credentials
- **외부 secret resolver** (`external_secrets.py`): `__external__:<provider>:<ref>` 마커로 Vault/ENV 동적 해석 (feature flag)
- **시스템 credential 분리** (M36, M39): `is_system=True && user_id IS NULL`. 일반 사용자는 `/api/credentials`로 보이지 않고 super_user 전용 `/api/system-credentials` 라우터로만 관리

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

각 ADR 본문은 `docs/design-docs/`에 있다.

---

## 현재 상태 요약

- **백엔드**: M39(2026-05 시점)까지 마이그레이션 적용. 멀티유저 인증(M36), MCP health polling(M26), Builder session FK SET NULL(M35), Message event streaming status(M34), Active branch(M29), Share links(M30/31), Refresh token rotation/GC(M37/38), System credential dedupe(M39)
- **프론트엔드**: 멀티유저 로그인/회원가입 UI, MCP 서버 관리, Skill 관리, Credential 관리, 채팅 SSE 스트리밍, 트리거 스케줄링, Builder 마법사
- **다음 단계**: `docs/marketplace-resources-prd.md` 기반 마켓플레이스(Agent/MCP/Skill 공유) 구축
- 자세한 태스크 현황은 `TASKS.md` 참조
- 기능 명세는 `docs/PRD.md` + `docs/PRD-screens.md` 참조
