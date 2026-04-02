# Moldy — 대화로 만드는 AI 에이전트 빌더

자연어로 원하는 업무를 설명하면 AI가 에이전트를 자동 구성하고, MCP 도구와 연결하여 반복 업무를 자동 처리합니다.

## 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Next.js 16, React 19, TailwindCSS v4, shadcn/ui, TanStack Query, Jotai |
| Backend | FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 16, Alembic |
| AI Core | LangChain 1.0+, LangGraph 1.0+, langchain-openai/anthropic/google |
| MCP | MCP Python SDK |
| Streaming | SSE (Server-Sent Events) |
| Infra | Docker Compose, mise (Python 3.12, Node 22) |

## 시작하기

### 사전 요구사항

- [mise](https://mise.jdx.dev/) (Python 3.12, Node 22 자동 관리)
- [Docker](https://www.docker.com/) (PostgreSQL용)
- [pnpm](https://pnpm.io/) (Node 패키지 매니저)
- OpenAI API Key (최소 1개 LLM 프로바이더 필요)

### 1. 런타임 설치

```bash
mise install
```

### 2. PostgreSQL 시작

```bash
docker compose up postgres -d
```

> 기본 접속 정보: `moldy:moldy@localhost:5432/moldy`

### 3. Backend 실행

```bash
cd backend

# 의존성 설치
uv sync

# 환경변수 설정
cp .env.example .env
# .env 파일에서 OPENAI_API_KEY 등 설정

# DB 마이그레이션
uv run alembic upgrade head

# 개발 서버 실행 (포트 8001)
uv run uvicorn app.main:app --reload --port 8001
```

> 서버 시작 시 기본 모델(GPT-4o, Claude Sonnet 4, Gemini Flash)과 템플릿 4개가 자동 생성됩니다.

API 문서: http://localhost:8001/docs

### 4. Frontend 실행

```bash
cd frontend

# 의존성 설치
pnpm install

# 개발 서버 실행 (포트 3000)
pnpm dev
```

브라우저에서 http://localhost:3000 접속

### Docker Compose 전체 실행 (대안)

```bash
# 환경변수 설정
export OPENAI_API_KEY=sk-your-key

# 전체 스택 실행
docker compose up -d

# Frontend: http://localhost:3000
# Backend API: http://localhost:8001
# API Docs: http://localhost:8001/docs
```

## 주요 기능 (P1 — PoC)

| 기능 | 설명 |
|------|------|
| 대화형 에이전트 생성 | AI와 대화하며 에이전트를 자동 구성 |
| 에이전트 채팅 | SSE 스트리밍으로 실시간 대화, 도구 호출 시각화 |
| MCP 도구 연동 | 외부 MCP 서버 등록 + 커스텀 도구 직접 정의 |
| 에이전트 템플릿 | 사전 제작 템플릿으로 빠른 생성 |
| LLM 모델 선택 | OpenAI, Anthropic, Google 등 멀티 프로바이더 지원 |
| 토큰 사용량 추적 | 에이전트별 토큰 소비량과 추정 비용 표시 |

## 프로젝트 구조

```
natural-mold/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app
│   │   ├── models/              # SQLAlchemy ORM (11 tables)
│   │   ├── schemas/             # Pydantic request/response
│   │   ├── routers/             # API endpoints (25 endpoints)
│   │   ├── services/            # Business logic
│   │   ├── agent_runtime/       # LangChain/LangGraph 실행 엔진
│   │   └── seed/                # 기본 모델/템플릿 데이터
│   └── tests/                   # pytest (11 tests)
│
├── frontend/
│   └── src/
│       ├── app/                 # Next.js App Router (10 pages)
│       ├── components/          # UI 컴포넌트
│       └── lib/                 # API 클라이언트, hooks, stores
│
├── docker-compose.yml           # PostgreSQL + Backend + Frontend
└── docs/
    ├── PRD.md                   # 요구사항 문서
    └── PRD-screens.md           # 화면 설계
```

## API 엔드포인트

| 그룹 | 메서드 | 경로 | 설명 |
|------|--------|------|------|
| 에이전트 | GET | `/api/agents` | 목록 |
| | POST | `/api/agents` | 생성 |
| | GET/PUT/DELETE | `/api/agents/:id` | 상세/수정/삭제 |
| 대화형 생성 | POST | `/api/agents/create-session` | 세션 시작 |
| | POST | `/api/agents/create-session/:id/message` | 메시지 전송 |
| | POST | `/api/agents/create-session/:id/confirm` | 생성 확정 |
| 채팅 | GET/POST | `/api/agents/:id/conversations` | 대화 목록/생성 |
| | GET | `/api/conversations/:id/messages` | 메시지 목록 |
| | POST | `/api/conversations/:id/messages` | 메시지 전송 (SSE) |
| 도구 | GET | `/api/tools` | 목록 |
| | POST | `/api/tools/custom` | 커스텀 도구 등록 |
| | POST | `/api/tools/mcp-server` | MCP 서버 등록 |
| 모델 | GET/POST | `/api/models` | 목록/등록 |
| 템플릿 | GET | `/api/templates` | 목록 |
| 사용량 | GET | `/api/usage/summary` | 전체 사용량 요약 |

## 테스트

```bash
# Backend
cd backend && uv run python -m pytest -v

# Frontend
cd frontend && pnpm build  # TypeScript + 빌드 검증
```

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL 접속 URL (async) |
| `OPENAI_API_KEY` | O | OpenAI API Key (기본 모델) |
| `ANTHROPIC_API_KEY` | - | Anthropic API Key |
| `GOOGLE_API_KEY` | - | Google AI API Key |
| `ENCRYPTION_KEY` | - | API Key 암호화용 Fernet Key |
| `LANGSMITH_API_KEY` | - | LangSmith 트레이싱 |

## 라이선스

Private
