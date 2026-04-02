# Moldy — AI Agent Builder

노코드로 AI 에이전트를 만들고, 채팅하고, 스케줄링하는 웹 애플리케이션.
PoC 단계이며, 인증 없이 Mock User로 동작한다.

---

## 기술 스택

| 레이어 | 기술 | 버전 |
|--------|------|------|
| Frontend | Next.js (App Router) + React + TailwindCSS v4 + shadcn/ui | Next 16, React 19 |
| 상태관리 | TanStack Query (서버), Jotai (클라이언트) | |
| Backend | FastAPI + SQLAlchemy (async) + Alembic | FastAPI 0.115+, SA 2.0+ |
| AI Runtime | LangChain 1.x + LangGraph 1.x + LangSmith | |
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
│   │   ├── main.py              # FastAPI app factory + lifespan (시드, 스케줄러)
│   │   ├── config.py            # pydantic-settings (.env 기반)
│   │   ├── database.py          # async engine + session
│   │   ├── dependencies.py      # get_db, get_current_user (mock)
│   │   ├── scheduler.py         # APScheduler 싱글턴
│   │   ├── models/              # SQLAlchemy ORM 모델
│   │   ├── schemas/             # Pydantic request/response 스키마
│   │   ├── routers/             # FastAPI 라우터 (agents, tools, conversations, ...)
│   │   ├── services/            # 비즈니스 로직 레이어
│   │   ├── agent_runtime/       # LangChain/LangGraph 실행 엔진
│   │   │   ├── executor.py      # create_agent + astream
│   │   │   ├── model_factory.py # LLM 인스턴스 생성
│   │   │   ├── tool_factory.py  # 도구 인스턴스 생성 (prebuilt 레지스트리)
│   │   │   ├── streaming.py     # LangGraph → SSE 변환
│   │   │   ├── creation_agent.py # 대화형 에이전트 생성 메타 에이전트
│   │   │   ├── mcp_client.py    # MCP 서버 연결
│   │   │   ├── trigger_executor.py # 스케줄 트리거 실행
│   │   │   ├── naver_tools.py   # 네이버 검색 API 도구
│   │   │   ├── google_tools.py  # Google Custom Search 도구
│   │   │   └── google_workspace_tools.py # Gmail, Calendar, Chat Webhook
│   │   └── seed/                # 시드 데이터 (모델, 템플릿, 시스템 도구)
│   ├── alembic/                 # DB 마이그레이션
│   ├── tests/                   # pytest (aiosqlite in-memory)
│   ├── scripts/                 # 유틸리티 (google_oauth_setup.py 등)
│   ├── pyproject.toml
│   └── .env.example             # 환경변수 템플릿
│
├── frontend/
│   ├── src/
│   │   ├── app/                 # Next.js App Router 페이지
│   │   │   ├── page.tsx         # 대시보드 (에이전트 카드 그리드)
│   │   │   ├── agents/          # 에이전트 상세, 설정, 채팅, 생성
│   │   │   ├── tools/           # 도구 관리
│   │   │   ├── models/          # 모델 관리
│   │   │   └── usage/           # 사용량 대시보드
│   │   ├── components/          # UI 컴포넌트
│   │   │   ├── ui/              # shadcn/ui 기본 컴포넌트
│   │   │   ├── layout/          # 사이드바, 헤더
│   │   │   ├── agent/           # 에이전트 관련
│   │   │   ├── chat/            # 채팅 관련
│   │   │   ├── tool/            # 도구 관련
│   │   │   └── shared/          # 공용 컴포넌트
│   │   ├── lib/
│   │   │   ├── api/             # API 클라이언트 (도메인별 분리)
│   │   │   ├── hooks/           # TanStack Query hooks
│   │   │   ├── stores/          # Jotai atoms
│   │   │   ├── sse/             # SSE 스트리밍 클라이언트
│   │   │   ├── types/           # TypeScript 타입 정의
│   │   │   ├── providers/       # React context providers
│   │   │   └── utils.ts         # cn() 등 유틸리티
│   │   └── hooks/               # 범용 hooks
│   ├── package.json
│   └── AGENTS.md                # Next.js 16 주의사항
│
├── docs/
│   ├── PRD.md                   # 제품 요구사항 정의서
│   ├── PRD-screens.md           # 화면별 와이어프레임
│   └── tool-setup-guide.md      # 프리빌트 도구 API 키 설정 가이드
│
├── docker-compose.yml           # PostgreSQL + Backend + Frontend
├── TASKS.md                     # 태스크 목록 (Phase별)
└── .mise.toml                   # Python 3.12, Node 22
```

---

## 로컬 개발 환경 세팅

### 사전 요구사항

- [mise](https://mise.jdx.dev/) 설치 (Python, Node 버전 관리)
- Docker Desktop (PostgreSQL용)

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
cp .env.example .env  # API 키 설정
uv sync               # 의존성 설치 (.venv 자동 생성)
uv run alembic upgrade head   # DB 마이그레이션
uv run uvicorn app.main:app --reload --port 8001
# → http://localhost:8001/docs (Swagger UI)
# 시작 시 시드 데이터 자동 삽입 (모델, 템플릿, 시스템 도구)
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
Router (routers/)      → HTTP 엔드포인트, 요청/응답 변환
Service (services/)    → 비즈니스 로직, DB 쿼리
Model (models/)        → SQLAlchemy ORM
Schema (schemas/)      → Pydantic 입출력 스키마
```

- 모든 DB 접근은 async (`AsyncSession`)
- 의존성 주입: `Depends(get_db)`, `Depends(get_current_user)`
- 현재 인증 없음 — `get_current_user`는 Mock User 반환

### Backend: AI Runtime (agent_runtime/)

```
executor.py     → LangGraph 에이전트 생성 + 실행
model_factory   → provider별 LLM 인스턴스 (OpenAI, Anthropic, Google)
tool_factory    → 도구 인스턴스 생성 (prebuilt 레지스트리 + MCP + custom)
streaming       → LangGraph 이벤트 → SSE 포맷 변환
```

- 대화 히스토리: LangGraph `PostgresSaver` (체크포인트)
- 도구 타입: `prebuilt` (서버 빌트인), `mcp` (MCP 서버), `custom` (사용자 정의)
- 에이전트별 도구 설정: `agent_tools.config` JSON → `tool.auth_config`에 merge

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
| `users` | 사용자 (PoC: mock user 1명) |
| `agents` | AI 에이전트 (이름, 시스템 프롬프트, 모델 연결) |
| `models` | LLM 모델 정의 (provider, model_id) |
| `tools` | 도구 (prebuilt/mcp/custom, `is_system` 플래그) |
| `agent_tools` | 에이전트-도구 연결 (config JSON 포함) |
| `conversations` | 대화 세션 |
| `messages` | 대화 메시지 |
| `token_usages` | 토큰 사용량 추적 |
| `templates` | 에이전트 템플릿 |
| `agent_creation_sessions` | 대화형 에이전트 생성 세션 |
| `agent_triggers` | 스케줄 트리거 (cron/interval) |

마이그레이션: `backend/alembic/versions/` (Alembic)

---

## 환경 변수

`backend/.env.example` 참조. 최소 동작에 필요한 키:

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL async URL |
| `OPENAI_API_KEY` 또는 `ANTHROPIC_API_KEY` | O (하나 이상) | LLM 호출용 |
| `ENCRYPTION_KEY` | O | Fernet 키 (DB 내 API 키 암호화) |
| 나머지 (Naver, Google 등) | X | 해당 도구 사용 시에만 필요 |

---

## 빌트인 도구 시스템

시스템 도구(`is_system=True`)는 서버 시작 시 자동 시드된다.

| 도구 | 타입 | 필요 키 |
|------|------|---------|
| Web Search (DuckDuckGo) | prebuilt | 없음 |
| Web Scraper | prebuilt | 없음 |
| Current DateTime | prebuilt | 없음 |
| Naver 검색 (5종) | prebuilt | NAVER_CLIENT_ID/SECRET |
| Google 검색 (3종) | prebuilt | GOOGLE_API_KEY + GOOGLE_CSE_ID |
| Google Chat Webhook | prebuilt | GOOGLE_CHAT_WEBHOOK_URL |
| Gmail (2종) | prebuilt | Google OAuth2 토큰 |
| Calendar (3종) | prebuilt | Google OAuth2 토큰 |

서버 키가 설정되지 않은 도구는 에이전트별 `tool_configs`로 개별 키를 제공할 수 있다.

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

### Frontend

- TypeScript strict mode
- React 19 Server Components 우선, `'use client'` 최소화
- UI: shadcn/ui 컴포넌트 우선 사용
- any 금지 → unknown + 타입 가드
- barrel export(index.ts) 지양

---

## 현재 상태 요약

- **Phase 0~5, 7~11 완료**: 백엔드 API 전체, 프론트엔드 전체 화면, 빌트인 도구 카탈로그, 트리거/스케줄러, Docker 구동
- **남은 작업 (Phase 6)**: E2E 시나리오 검증, 접근성/키보드 네비게이션/성능 검증
- 자세한 태스크 현황은 `TASKS.md` 참조
- 기능 명세는 `docs/PRD.md` + `docs/PRD-screens.md` 참조
