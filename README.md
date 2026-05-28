<div align="center">

<img src="docs/images/moldy-mascot.webp" alt="Moldy 마스코트" width="160">

# Moldy

**대화로 만드는 AI 에이전트 빌더 — FastAPI + LangGraph + deepagents**

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)]()
[![Next.js](https://img.shields.io/badge/Next.js-16-black.svg)]()
[![React](https://img.shields.io/badge/React-19-61dafb.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)]()
[![LangGraph](https://img.shields.io/badge/LangGraph-1.0+-purple.svg)]()
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)]()
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[한국어](README.md) · [English](README_EN.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[Overview](#-overview) · [Quick Start](#-quick-start) · [기능](#-주요-기능) · [아키텍처](#-아키텍처)

</div>

---

## 🧐 Overview

**Moldy**는 자연어로 원하는 업무를 설명하면 AI가 에이전트를 자동 구성해 주는
노코드 AI 에이전트 빌더입니다. 코드 한 줄 없이 *대화*만으로 도구·스킬·트리거를
조합한 자동화 워크플로우를 만들고, 만든 에이전트와 그대로 채팅하거나 스케줄에
맞춰 실행할 수 있습니다.

### 무엇이 다른가

- **대화형 빌더** — 메타 에이전트가 사용자의 의도를 파악해 빌드 옵션을 단계적으로
  제안하고 합의된 시점에 실제 에이전트를 생성합니다. 폼을 채우는 대신 **요구사항을
  설명**하면 됩니다.
- **도구·스킬·MCP 통합 카탈로그** — 빌트인 검색/스크래퍼/캘린더/Gmail 같은
  prebuilt 도구, 외부 **MCP 서버**(stdio/HTTP), 사용자 정의 **Skill**(SKILL.md
  + 보조 파일)을 한 화면에서 관리합니다.
- **분기 가능한 대화** — LangGraph checkpointer 기반의 **fork & 시간여행**으로
  메시지 편집·재생성 시 새 분기로 갈라지고, 좌우 화살표로 형제 응답을 비교할 수
  있습니다.
- **HITL(Human-in-the-Loop)** — 도구 호출 승인, 사용자 입력 요청, 명확화 질문
  같은 인터럽트 패턴을 **카운트다운 + 자동 연장** UX로 처리합니다.
- **노코드 트리거** — cron · interval 기반 스케줄 트리거로 에이전트를 정해진
  시간에 자동 실행하고 결과를 알림으로 전달합니다.
- **공개 공유 링크** — 한 번의 클릭으로 대화를 read-only 링크로 공유하면 누구나
  로그인 없이 에이전트의 사고 과정을 추적할 수 있습니다.

## 🚀 Quick Start

### 사전 요구사항

- [mise](https://mise.jdx.dev/) — Python 3.12 · Node 22 자동 관리
- [Docker](https://www.docker.com/) — PostgreSQL 16 컨테이너용
- [pnpm](https://pnpm.io/) — Node 패키지 매니저
- LLM API 키 — OpenAI / Anthropic / OpenRouter / OpenAI-compatible(LiteLLM 등) 중 하나. ENV에 넣을 필요 없이 **부팅 후 UI에서 등록**합니다 (ADR-013)

### 로컬 개발

```bash
# 1. 런타임 설치
mise install                          # Python 3.12 + Node 22

# 2. PostgreSQL 시작
docker compose up postgres -d         # localhost:5432, moldy:moldy/moldy

# 3. Backend
cd backend
cp .env.example .env                  # ENCRYPTION_KEYS / JWT_SECRET 등 입력 (LLM 키는 UI에서 등록)
uv sync                               # 의존성 설치
uv run alembic upgrade head           # DB 마이그레이션 (현재 head: m45)
uv run uvicorn app.main:app --reload --reload-dir app --port 8001
# → http://localhost:8001/docs (Swagger UI)

# 4. Frontend (새 터미널)
cd frontend
pnpm install
pnpm dev
# → http://localhost:3000
```

서버 시작 시 기본 모델(GPT-5.5, Claude Sonnet 4.6, Gemini 등) + 시스템 도구
+ 에이전트 템플릿이 자동 시드됩니다. 단, **에이전트를 만들고 쓰려면 아래 운영자
초기 설정이 필요**합니다.

### 서버 기동 후 초기 설정 (운영자)

LLM 키는 ENV가 아닌 UI에서 등록하고, system 기능(빌더·어시스턴트·이미지)은
운영자가 사용할 모델을 직접 골라야 동작합니다 (ADR-013/016/019).

1. **첫 계정 = 운영자** — http://localhost:3000 에서 회원가입. 첫 사용자는
   `super_user`로 자동 승격됩니다 (ADR-016, `ALLOW_FIRST_USER_AS_ADMIN=true`;
   운영 환경에서는 계정 생성 후 꺼주세요).
2. **LLM 크리덴셜 등록** — `/settings/system-credentials`에서 OpenAI ·
   Anthropic · OpenRouter · OpenAI-compatible(LiteLLM 등) 키를 등록합니다.
3. **System LLM 모델 선택 (ADR-019, 필수)** — `/settings/system-llm`에서
   `text_primary` · `text_fallback` · `image` 세 슬롯의 모델을 고릅니다.
   크리덴셜 선택 → "모델 목록 불러오기" → 모델 선택. **이 설정 전에는 빌더 ·
   어시스턴트 · 이미지 생성이 동작하지 않습니다**(조용한 실패 없이 명시적 에러).
4. **에이전트용 모델 연결** — `/models`에서 일반 에이전트가 쓸 모델에 크리덴셜을
   연결하거나 discovery로 자동 등록합니다.

이후 대화형 빌더(`/agents`)로 에이전트를 만들고 채팅할 수 있습니다. 일반
사용자는 본인 키를 `/credentials`에서 등록해 사용합니다.

### Docker Compose 전체 실행

```bash
docker compose up -d                  # postgres + backend + frontend
# 이후 위의 "서버 기동 후 초기 설정"을 따라 운영자 온보딩을 진행하세요.
```

### 검증 명령

```bash
# Backend
cd backend
uv run ruff check .                   # 린트
uv run pytest                         # 단위 테스트 (aiosqlite, Postgres 불필요)
uv run pytest -m integration          # 통합 테스트 (Postgres 필요)

# Frontend
cd frontend
pnpm lint                             # ESLint
pnpm exec tsc --noEmit                # 타입체크
pnpm test --run                       # vitest (jsdom)
pnpm build                            # 프로덕션 빌드
```

> **Pre-push hook**: `git push` 시점에 `.husky/pre-push`가 backend pytest +
> frontend vitest를 자동 실행하여 회귀가 push되지 않도록 차단합니다. 우회는
> `git push --no-verify` (WIP 브랜치 한정).

## 📸 Screenshots

> 준비 중. 주요 화면은 `docs/PRD-screens.md`에 와이어프레임으로 정리되어 있습니다.

## ✨ 주요 기능

<details>
<summary><b>🤖 에이전트 시스템</b></summary>

- **deepagents 엔진** — `create_deep_agent` + LangGraph 컴파일된 그래프 위에
  메시지 트리, 분기, 체크포인트 관리
- **대화형 빌더** — 메타 에이전트가 자연어 요구사항을 인터뷰하며 빌드 옵션을
  제안 (`agent_runtime/creation_agent.py`)
- **에이전트 템플릿** — 사전 정의된 에이전트로 즉시 시작
- **Sub-agents** — 다단계 위임 (에이전트가 다른 에이전트를 도구처럼 호출)
- **미들웨어 시스템** — 22종 미들웨어 카탈로그 (context engineering, planning,
  safety, reliability, provider-specific)
- **모델 fallback 체인** — primary 모델 실패 시 대체 모델 자동 호출 (최대 5단계)

</details>

<details>
<summary><b>💬 채팅 + 분기</b></summary>

- **SSE 스트리밍** — 토큰 단위 실시간 출력, 도구 호출 시각화
- **LangGraph fork** — 사용자 메시지 편집 / 어시스턴트 재생성 시 새 분기 생성,
  체크포인트 ID 기반 시간여행
- **BranchPicker** — `<N/M>` 좌우 화살표로 형제 응답 비교 (assistant-ui 통합)
- **HITL countdown** — 도구 승인 / 사용자 입력 / 명확화 질문 인터럽트에 카운트다운
  타이머 + 만료 시 자동 연장 + 긴급 상태 스타일
- **메시지 액션** — 복사·편집·재생성·thumb 피드백·삭제·검색
- **Mermaid / KaTeX / 코드 블록** — 마크다운 렌더링, 이미지 lightbox
- **첨부 파일** — 이미지/문서 업로드 후 메시지에 인라인 표시
- **공개 공유 링크** — read-only 페이지 (`/shared/{token}`), 소프트 삭제로 즉시
  무효화

</details>

<details>
<summary><b>🛠️ 도구 · 스킬 · MCP</b></summary>

- **빌트인 도구 카탈로그** — DuckDuckGo / 웹 스크래퍼 / 현재 시각 / Naver 검색
  5종 / Google CSE 3종 / Gmail 2종 / Calendar 3종 / Google Chat Webhook
- **MCP 통합** — stdio + HTTP 서버 등록, `langchain-mcp-adapters` 기반,
  import/export, health check polling
- **Skill 시스템** — SKILL.md(YAML frontmatter) + 보조 파일을 묶은 스킬 패키지,
  multi-file 인라인 에디터, scratch/upload/import 3가지 생성 방식
- **사용자 정의 도구** — Pydantic 스키마로 도구 파라미터 정의

</details>

<details>
<summary><b>🔐 크리덴셜 · 모델 관리</b></summary>

- **Cipher V2 암호화** — HKDF-SHA256 + AES-256-GCM, 단일 블롭 Base64
- **Vault 통합** — `hvac` 기반 external secrets 지원
- **System / User 크리덴셜 분리** — 운영자 관리 vs 사용자 개인 키
- **한국 서비스 8종** — SRT · KTX · 산림청 숲길 · KIPRIS · DART · ODsay · 쿠팡 파트너스 · K-Skill 프록시
- **모델 discovery** — 크리덴셜로 LLM API에 직접 질의해 사용 가능 모델 + 가격
  + 컨텍스트 윈도우 자동 가져오기
- **모델 health check** — 주기적 probe로 모델 가용성 모니터링
- **벤치마크 랭킹** — LMArena · LiveBench · AAIndex 점수 표시

</details>

<details>
<summary><b>⏰ 트리거 · 사용량 · 관측성</b></summary>

- **스케줄 트리거** — APScheduler 기반 cron / interval, 에이전트별 입력 메시지
  지정, Google Chat Webhook 알림
- **토큰 사용량 추적** — 에이전트별 / 모델별 / 일별 토큰 + 추정 비용
- **Daily spend** — 사용자 / 에이전트 / 모델 단위 일별 집계
- **LangSmith 트레이싱** — 실행 트레이스 자동 전송

</details>

<details>
<summary><b>🎨 Frontend</b></summary>

- **Next.js 16 + React 19** — App Router, Server Components 우선
- **TailwindCSS v4 + shadcn/ui** — 디자인 토큰 기반 (`--primary-strong` emerald),
  ADR-010 디자인 시스템
- **DialogShell 패턴** — 모든 다이얼로그를 토큰 사이즈(`md`/`lg`/`xl`/`console`)로
  통일, lightbox용 `srOnly` 헤더 prop
- **TanStack Query** — 서버 상태 관리 (캐싱 + invalidation)
- **Jotai** — 클라이언트 상태 (사이드바, 우측 패널 등)
- **assistant-ui** — 채팅 메시지 트리, BranchPicker, ActionBar
- **i18n** — next-intl 기반, 한국어 기본
- **반응형** — 모바일 사이드바 = Sheet, 데스크톱 = SidebarProvider

</details>

<details>
<summary><b>🛒 마켓플레이스</b></summary>

- **카탈로그** — Agent / MCP 서버 / Skill을 공개 마켓플레이스에 게시하고 한 클릭으로 설치
- **원본-설치본 분리** — 설치 시 사용자 계정에 독립 복사본 생성, 원본 업데이트와 독립 동작
- **버전 스냅샷** — `marketplace_versions` 테이블에 immutable 버전 이력 관리
- **Credential 바인딩** — Skill별 필요 credential을 설치 시점에 사용자 계정 키로 매핑
- **모더레이션** — super_user가 `/marketplace/admin/moderation`에서 공개 심사

</details>

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                      │
│  app/ (라우트) → components/ (UI) → lib/api,hooks,stores        │
│  ↓ fetch + SSE (EventSource)                                   │
└─────────────────────────────────────────────────────────────────┘
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│                       Backend (FastAPI)                         │
│  routers/ → services/ → models/ (SQLAlchemy 2.0 async)         │
│                                                                 │
│  agent_runtime/                                                 │
│    ├ builder_v3/ (대화형 메타 빌더 — 최신)                      │
│    ├ executor (create_deep_agent + astream)                     │
│    ├ streaming (LangGraph events → SSE chunks, orjson)          │
│    ├ event_broker (이벤트 브로드캐스트)                          │
│    ├ tool_factory (prebuilt + MCP + custom 통합)               │
│    ├ model_factory (provider별 LLM)                             │
│    └ trigger_executor (스케줄 → 메시지 실행)                     │
│                                                                 │
│  scheduler.py — APScheduler 싱글턴                              │
└─────────────────────────────────────────────────────────────────┘
                  ↓                              ↓
       PostgreSQL (모델/대화/도구)      LangGraph PostgresSaver
                                        (체크포인트 = 메시지 트리)
```

### 3계층 구조

- **Router** (`app/routers/`) — HTTP 엔드포인트, 요청·응답 변환
- **Service** (`app/services/`) — 비즈니스 로직, DB 쿼리, 트랜잭션
- **Model** (`app/models/`) — SQLAlchemy ORM, ~40개 테이블 (m45 기준)

### Frontend 패턴

- API 클라이언트 (`lib/api/`) → TanStack Query 훅 (`lib/hooks/`) → 컴포넌트
- 채팅 SSE는 `lib/sse/`의 EventSource 래퍼로 토큰 단위 처리
- 디자인 토큰은 `lib/design-tokens.ts` + `app/globals.css` (oklch 기반)

자세한 내용은 [`CLAUDE.md`](CLAUDE.md) (개발자 핸드북) 참고.

## 📁 프로젝트 구조

```
natural-mold/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 앱 팩토리 + lifespan
│   │   ├── config.py            # pydantic-settings (.env)
│   │   ├── database.py          # async engine + session
│   │   ├── dependencies.py      # get_db, get_current_user, require_super_user, verify_csrf
│   │   ├── scheduler.py         # APScheduler 싱글턴
│   │   ├── models/              # SQLAlchemy ORM
│   │   ├── schemas/             # Pydantic 스키마
│   │   ├── routers/             # HTTP 라우터
│   │   ├── services/            # 비즈니스 로직
│   │   ├── credentials/         # Cipher V2 + 도메인
│   │   ├── agent_runtime/       # AI 실행 엔진
│   │   └── seed/                # 시드 데이터
│   ├── alembic/versions/        # 마이그레이션 (m45까지)
│   └── tests/                   # pytest (aiosqlite in-memory)
├── frontend/
│   └── src/
│       ├── app/                 # Next.js App Router (23+ 라우트)
│       ├── components/          # UI 컴포넌트
│       └── lib/                 # api, hooks, stores, sse, types
├── docs/
│   ├── PRD.md                   # 제품 요구사항
│   ├── PRD-screens.md           # 화면 와이어프레임
│   ├── ARCHITECTURE.md          # 시스템 아키텍처
│   ├── design-docs/             # ADR (디자인 결정)
│   ├── marketplace-resources-prd.md  # 마켓플레이스 PRD
│   └── tool-setup-guide.md      # 도구 API 키 설정
├── tasks/                       # 작업 메모 + archive/
├── docker-compose.yml
├── HANDOFF.md                   # 세션 인계 문서
├── TASKS.md                     # Phase별 태스크 트래커
├── CLAUDE.md                    # 개발자 핸드북
├── CONTRIBUTING.md
└── SECURITY.md
```

## 🔧 환경변수

전체 목록은 `backend/.env.example` 참고. 최소 동작 키:

| 변수 | 필수 | 설명 |
|------|------|------|
| `DATABASE_URL` | O | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `ENCRYPTION_KEY` | O | Cipher V2 마스터 키 (HKDF-SHA256 + AES-256-GCM) |
| `JWT_SECRET` | O | JWT HS256 서명 키 (ADR-016 멀티유저 인증) |
| LLM 키 (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 등) | - | UI Credentials에서 등록 권장 (ADR-013). ENV는 dev bootstrap용 선택값 |
| `LANGSMITH_API_KEY` | - | LangSmith 트레이싱 (선택) |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | - | 네이버 검색 도구 |
| `GOOGLE_API_KEY` / `GOOGLE_CSE_ID` | - | Google CSE 도구 |
| Google OAuth2 토큰 | - | Gmail / Calendar 도구 (`scripts/google_oauth_setup.py`) |

도구별 키 설정은 [`docs/tool-setup-guide.md`](docs/tool-setup-guide.md) 참고.

## 🤝 Contributing

기여 방법은 [`CONTRIBUTING.md`](CONTRIBUTING.md) 참고. 보안 이슈는
[`SECURITY.md`](SECURITY.md) 절차대로.

## 📄 License

[MIT](LICENSE) — Copyright (c) 2026 Moldy contributors.

---

<div align="center">

세부 컨벤션·디자인 토큰·long-horizon 워크플로우는 [`CLAUDE.md`](CLAUDE.md)와
[`frontend/AGENTS.md`](frontend/AGENTS.md)를 참고하세요.

</div>
