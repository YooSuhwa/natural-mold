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

한국어 · [English](README.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[Overview](#-overview) · [빠른 답변](#-빠른-답변) · [Quick Start](#-quick-start) · [신뢰 근거](#-품질보안문서화-신호) · [기능](#-주요-기능) · [아키텍처](#-아키텍처)

**마지막 업데이트:** 2026년 6월 2일 · **Repository:** [YooSuhwa/natural-mold](https://github.com/YooSuhwa/natural-mold) · **License:** [MIT](LICENSE)

</div>

---

## 🧐 Overview

**Moldy**는 자연어로 원하는 업무를 설명하면 AI가 에이전트를 자동 구성해 주는
노코드 AI 에이전트 빌더입니다. 코드 한 줄 없이 *대화*만으로 도구·스킬·트리거를
조합한 자동화 워크플로우를 만들고, 만든 에이전트와 그대로 채팅하거나 스케줄에
맞춰 실행할 수 있습니다.

### Moldy란?

Moldy는 웹 UI에서 AI 에이전트를 만들고, 설정하고, 채팅하고, 스케줄링할 수 있는
오픈소스 self-hostable AI 에이전트 빌더입니다. 이 프로젝트는 Next.js 16 + React
19 프론트엔드, FastAPI 백엔드, PostgreSQL 16, LangGraph 1.x, `deepagents`의
`create_deep_agent` 런타임을 결합합니다. Moldy는 멀티유저 운영을 전제로 설계되어
있으며, ADR-016에 따라 JWT 인증, HttpOnly cookie, CSRF double-submit 보호,
refresh token rotation, 시스템 리소스 관리를 위한 `super_user` 역할을 적용했습니다.
이 monorepo에는 채팅 스트리밍, 메시지 분기, credential 관리, MCP 서버 통합,
skill 패키지, 마켓플레이스 설치, 스케줄 트리거, 사용량 추적이 포함됩니다.

### 프로젝트 사실

| 항목 | 현재 README 기준 |
|---|---|
| 프로젝트 유형 | 오픈소스 웹 애플리케이션 및 monorepo |
| 주요 사용 사례 | 노코드 AI 에이전트 생성, 채팅, 스케줄링, 도구/스킬 오케스트레이션 |
| Backend | FastAPI 0.115+, SQLAlchemy 2.0 async, Alembic, Python 3.12 |
| Frontend | Next.js 16, React 19, TailwindCSS v4, shadcn/ui |
| AI runtime | LangGraph 1.x + `create_deep_agent` 기반 `deepagents` |
| Database | PostgreSQL 16, 현재 마이그레이션 head는 `m52`로 문서화 |
| 인증 | JWT HS256, HttpOnly cookie, CSRF double-submit, refresh token rotation, `super_user` |
| License | MIT |

### 무엇이 다른가

- **대화형 빌더** — 메타 에이전트가 사용자의 의도를 파악해 빌드 옵션을 단계적으로
  제안하고 합의된 시점에 실제 에이전트를 생성합니다. 폼을 채우는 대신 **요구사항을
  설명**하면 됩니다.
- **도구·스킬·MCP 통합 카탈로그** — 빌트인 검색/스크래퍼/캘린더/Gmail 같은
  prebuilt 도구, 레지스트리 기반 **MCP 서버**(stdio/SSE/Streamable HTTP),
  사용자 정의 **Skill**(SKILL.md + 보조 파일)을 한 화면에서 관리합니다.
- **분기 가능한 대화** — LangGraph checkpointer 기반의 **fork & 시간여행**으로
  메시지 편집·재생성 시 새 분기로 갈라지고, 좌우 화살표로 형제 응답을 비교할 수
  있습니다.
- **HITL(Human-in-the-Loop)** — 도구 호출 승인, 사용자 입력 요청, 명확화 질문
  같은 인터럽트 패턴을 **카운트다운 + 자동 연장** UX로 처리합니다.
- **노코드 트리거** — cron · interval 기반 스케줄 트리거로 에이전트를 정해진
  시간에 자동 실행하고 결과를 알림으로 전달합니다.
- **공개 공유 링크** — 한 번의 클릭으로 대화를 read-only 링크로 공유하면 누구나
  로그인 없이 에이전트의 사고 과정을 추적할 수 있습니다.

## ❓ 빠른 답변

### Moldy는 무엇을 하나요?

Moldy는 자연어 요구사항을 실행 가능한 AI 에이전트로 바꿉니다. 사용자는 원하는
워크플로우를 설명하고, 대화형 빌더가 제안하는 에이전트 설정을 검토한 뒤 도구,
스킬, MCP 도구, credential을 연결할 수 있습니다. 이후 에이전트를 채팅에서 실행하거나
cron/interval 트리거로 예약 실행할 수 있으며, 분기 가능한 대화, SSE 스트리밍, 도구
호출 승인 흐름, 공개 read-only 공유 링크, 사용자별 credential 격리를 지원합니다.

### Moldy는 누구를 위한 프로젝트인가요?

Moldy는 완전 managed SaaS만 쓰기보다 로컬 또는 self-hosted 에이전트 빌더를 원하는
개발자, 운영자, 내부 도구 팀을 위한 프로젝트입니다. README는 PostgreSQL, Python
3.12, Node 22, `uv`, `pnpm`을 실행할 수 있는 독자를 기준으로 작성되어 있지만,
제품 UI는 코딩하지 않는 사용자도 guided setup, credential, 도구, 스킬, 스케줄을
통해 에이전트를 조립할 수 있도록 설계되어 있습니다.

### Moldy는 credential과 시스템 권한을 어떻게 다루나요?

Moldy는 운영자가 관리하는 시스템 리소스와 사용자별 리소스를 분리합니다. System
credentials와 System LLM settings는 `super_user` 계정이 관리하고, 일반 사용자는
개인 credential을 `/credentials`에서 등록합니다. Credential payload는
HKDF-SHA256과 AES-256-GCM을 사용하는 Cipher V2로 암호화되며, 런타임 접근은 명시적인
도구, 모델, MCP, skill binding을 통해 이뤄집니다.

### README의 주장들은 어디에서 검증할 수 있나요?

Moldy의 아키텍처와 보안 관련 설명은 repository 내부 문서로 검증할 수 있습니다.
아키텍처 의사결정은 [`docs/design-docs/`](docs/design-docs/)에, 상위 시스템 구조는
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)에, 보안 신고 및 배포 hardening은
[`SECURITY.md`](SECURITY.md)에 정리되어 있습니다. 이 README의 검증 명령과
pre-push hook은 backend/frontend 테스트 스위트를 반복 실행할 수 있는 경로를 제공합니다.

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
uv run alembic upgrade head           # DB 마이그레이션 (현재 head: m52)
uv run uvicorn app.main:app --reload --reload-dir app --port 8001
# → http://localhost:8001/docs (Swagger UI)

# 4. Frontend (새 터미널)
cd frontend
cp .env.example .env.local            # NEXT_PUBLIC_API_BASE_URL / E2E 계정 기본값
pnpm install
pnpm dev
# → http://localhost:3000
```

서버 시작 시 기본 모델(GPT-5.5, Claude Sonnet 4.6, Gemini 등), 시스템 도구,
에이전트 템플릿, 로컬 Playwright E2E 계정이 자동 시드됩니다. 단,
**에이전트를 만들고 쓰려면 아래 운영자 초기 설정이 필요**합니다.

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

### Worktree 개발 포트/CORS 규칙

git worktree에서 작업할 때는 먼저 `bash scripts/worktree-setup.sh`를 실행해
`backend/.env`와 `backend/data`가 main checkout을 가리키는 symlink인지 맞춥니다.
같은 PostgreSQL, `ENCRYPTION_KEYS`, `JWT_SECRET`을 공유해야 기존 credential 복호화와
로그인 세션이 깨지지 않습니다.

backend/frontend dev 서버는 **frontend port, backend port, CORS origin,
`NEXT_PUBLIC_API_BASE_URL`을 한 세트로** 맞춰야 합니다. 기본 권장 조합:

```bash
# backend
cd backend
uv run uvicorn app.main:app --reload --reload-dir app --port 8001

# frontend
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

여러 worktree를 동시에 띄울 때는 포트 쌍을 명시합니다:

```bash
# backend (:8010)
cd backend
CORS_ALLOWED_ORIGINS=http://localhost:3010,http://127.0.0.1:3010 \
  uv run uvicorn app.main:app --reload --reload-dir app --port 8010

# frontend (:3010)
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8010 pnpm dev -- --port 3010
```

Next.js가 포트 충돌로 임의 포트를 고르게 두면 CORS/cookie/CSRF가 어긋날 수
있으므로 항상 `pnpm dev -- --port <port>`로 고정하세요. 여러 backend를 같은 DB에
동시에 붙이면 APScheduler/trigger 작업이 중복 실행될 수 있어 장시간 동시 실행은
주의가 필요합니다.

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
pnpm test:e2e                         # Playwright E2E
```

## ✅ 품질·보안·문서화 신호

Moldy는 기술적 의사결정과 운영 리스크를 repository 안에 문서화합니다. 따라서 README의
설명을 홍보 문구가 아니라 실제 문서와 검증 명령으로 확인할 수 있습니다. 가장 강한
신뢰 신호는 ADR 기록, 명시적인 보안 정책, 재현 가능한 테스트 명령, 로컬 운영자 초기
설정 절차입니다. E-E-A-T 관점에서 이 README는 setup 세부사항으로 구현 경험을,
아키텍처와 ADR 링크로 전문성을, repository-local evidence로 권위를, 보안 및 검증
워크플로우로 신뢰성을 드러냅니다.

| 신호 | 근거 | 의미 |
|---|---|---|
| 아키텍처 의사결정 | [`docs/design-docs/`](docs/design-docs/)에는 멀티유저 인증 ADR-016, System LLM settings ADR-019 등이 포함됩니다 | 런타임, 인증, credential, UI 결정의 이유와 시점을 추적할 수 있습니다 |
| 시스템 아키텍처 | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)는 Next.js 프론트엔드, FastAPI 백엔드, PostgreSQL 데이터 계층, LangGraph/deepagents 런타임을 설명합니다 | README보다 자세한 설계 기준을 제공합니다 |
| 보안 프로세스 | [`SECURITY.md`](SECURITY.md)는 비공개 취약점 신고, 응답 목표, 배포자 hardening 체크를 문서화합니다 | 보안 신고 절차와 운영 책임을 명시합니다 |
| 검증 워크플로우 | 이 README는 backend lint/test, frontend lint/typecheck/test/build, integration test, Playwright E2E 명령을 나열합니다 | 유지보수자와 도입자가 같은 검증 경로를 재현할 수 있습니다 |
| 운영 설정 | Quick Start는 로컬 개발, worktree CORS 규칙, Docker Compose, E2E seed auth, System LLM 설정, MCP registry 설정을 분리합니다 | self-hosted 또는 multi-worktree 개발에서 생기는 모호함을 줄입니다 |

### Playwright E2E 인증

E2E는 테스트마다 로그인 폼을 통과하지 않고, Playwright global setup에서 한 번 API
로그인 세션을 만든 뒤 `storageState`를 모든 브라우저 컨텍스트에 주입하는 방식을
사용합니다. `backend/.env.example`은 로컬 개발용으로 `E2E_SEED_USER_ENABLED=true`를
켜 두며, 백엔드 시작 시 아래 더미 super_user를 DB에 생성하거나 갱신합니다.
`APP_ENV=production`에서는 이 seed가 자동으로 스킵됩니다.

```bash
E2E_USER_EMAIL=playwright-e2e@moldy.dev
E2E_USER_PASSWORD=correct horse battery staple 42
E2E_USER_NAME=E2E User
```

frontend 환경 파일도 같은 전용 테스트 계정 값을 사용합니다:

```bash
cd frontend
cp .env.example .env.local
# 필요 시 E2E_USER_EMAIL / E2E_USER_PASSWORD 수정
pnpm test:e2e
```

권장 흐름은 `login → register fallback → login → e2e/.auth/user.json 저장`입니다.
`frontend/e2e/.auth/`는 생성 산출물이므로 커밋하지 않습니다. API로 직접
생성/수정하는 E2E setup 코드는 로그인 응답의 `csrf_token`을
`X-CSRF-Token` header로 넣어야 합니다.

> **Pre-push hook**: `git push` 시점에 `.husky/pre-push`가 backend pytest +
> frontend vitest를 자동 실행하여 회귀가 push되지 않도록 차단합니다. 우회는
> `git push --no-verify` (WIP 브랜치 한정).

### Tavily + Deep Research

Tavily hosted search tool(`tavily_search`)과 Deep Research 마켓플레이스 skill이
연동되어 있습니다. backend `.env`에 `TAVILY_API_KEY`를 두면 Deep Research skill이
`tavily_search`를 **런타임 tool dependency로 자동 주입**받아, 사용자가 별도로 도구를
붙이지 않아도 citation 기반 멀티스텝 웹 리서치를 수행합니다. (설계 배경:
`docs/superpowers/plans/2026-05-31-deep-research-tavily.md`)

### MCP 레지스트리와 MCP Secret

`/mcp-servers` → **새 MCP 서버**에서 레지스트리 프리셋을 고르면 transport, URL,
stdio command/env template이 자동으로 채워지고, 저장 전 **도구 프로브**로 실제 노출
도구를 확인할 수 있습니다. 현재 프리셋은 GitHub, Linear, Atlassian Jira, Slack,
Notion과 로컬 first-party MCP(Hancom Groupware, Hancom Mile Meeting, Hancom Org
Chart, Maepsi)를 포함합니다.

인증이 필요한 first-party MCP 프리셋은 `/credentials`에서 `MCP Secret` 타입
credential을 만든 뒤 마법사 **인증** 탭에서 연결합니다. Moldy는 연결/실행 시
`secret` 값을 `X-Moldy-Credential` 헤더로 자동 전달합니다. 수동 MCP 서버를 등록할
때도 헤더나 stdio 환경 변수 값에 `{{ $credentials.<field> }}` 형식으로 연결된
credential 필드를 보간할 수 있습니다.

로컬 first-party MCP 프리셋의 기본 URL은 `localhost:18001`~`18004` 대역입니다.
이 서버들은 `docker compose up`에 포함되지 않으므로, 해당 프리셋을 쓰려면 MCP 서버
프로세스를 별도로 실행한 뒤 프로브하세요.

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

- **SSE 스트리밍** — 토큰 단위 실시간 출력, 도구 호출 시각화. 스트리밍 중
  코드 블록 plain 렌더 + SSE 큐 O(1) 처리 등으로 장문 응답 성능 최적화
- **IME-safe 입력창** — 한글 등 조합형 입력 중 Enter/편집/재생성이 조합 문자열을
  깨뜨리지 않도록 composer 상태를 안전하게 동기화
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

- **빌트인 도구 카탈로그** — DuckDuckGo / 웹 스크래퍼 / 현재 시각 / 상대 날짜
  해석(`resolve_relative_date`) / Tavily 검색 / Naver 검색 5종 / Google CSE 3종 /
  Gmail 보내기 / Google 캘린더 / Google Chat Webhook / HTTP 요청
- **MCP 통합** — stdio + SSE + Streamable HTTP 서버 등록,
  `langchain-mcp-adapters` 기반 import/export, health check polling
- **MCP 레지스트리 프리셋** — GitHub / Linear / Jira / Slack / Notion /
  Hancom / Maepsi 서버를 `/mcp-servers` 마법사에서 선택하고 저장 전 도구 프로브
- **MCP Secret credential** — first-party MCP 서버에 per-user secret을
  `X-Moldy-Credential` 헤더로 자동 전달
- **Skill 시스템** — SKILL.md(YAML frontmatter) + 보조 파일을 묶은 스킬 패키지,
  multi-file 인라인 에디터, scratch/upload/import 3가지 생성 방식
- **Skill 런타임 의존성** — Skill이 선언한 tool dependency를 에이전트 실행 시
  자동 주입 (예: Deep Research → Tavily). 사용자가 도구를 수동으로 붙일 필요 없음
- **사용자 정의 도구** — Pydantic 스키마로 도구 파라미터 정의

</details>

<details>
<summary><b>🔐 크리덴셜 · 모델 관리</b></summary>

- **Cipher V2 암호화** — HKDF-SHA256 + AES-256-GCM, 단일 블롭 Base64
- **Vault 통합** — `hvac` 기반 external secrets 지원
- **System / User 크리덴셜 분리** — 운영자 관리 vs 사용자 개인 키
- **MCP Secret** — 로컬 first-party MCP 서버용 per-user secret credential
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
- **스케줄 가드레일** — 최대 실행 횟수(`max_runs`), 종료 시각(`end_at`),
  연속 실패 시 자동 일시정지(`auto_pause_after_failures`)
- **대화 정책** — 트리거마다 새 대화 생성 / 지정 대화 재사용 선택
- **실행 이력** — `agent_trigger_runs`에 실행별 source / 출력 미리보기 /
  소요시간 / thread·checkpoint·trace ID 기록
- **토큰 사용량 추적** — 에이전트별 / 모델별 / 일별 토큰 + 추정 비용
- **Daily spend** — 사용자 / 에이전트 / 모델 단위 일별 집계
- **트레이싱** — LangSmith 자동 전송 + Langfuse 외부 트레이스 연동
  (`message_events`에 external trace provider/id/url 기록)

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
- **Tool dependency 표시** — Skill이 요구하는 도구(예: Tavily)를 설치 마법사에서
  안내하고 실행 시 자동 주입
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
- **Model** (`app/models/`) — SQLAlchemy ORM, 36개 테이블 (m52 기준)

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
│   ├── alembic/versions/        # 마이그레이션 (m52까지)
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
| `TAVILY_API_KEY` | - | Tavily 검색 / Deep Research skill용 hosted 키 (선택) |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | - | 네이버 검색 도구 |
| `GOOGLE_API_KEY` / `GOOGLE_CSE_ID` | - | Google CSE 도구 |
| Google OAuth2 토큰 | - | Gmail / Calendar 도구 (`scripts/google_oauth_setup.py`) |

도구별 키 설정은 [`docs/tool-setup-guide.md`](docs/tool-setup-guide.md) 참고.

## 🧩 구조화 데이터 (JSON-LD)

Moldy README를 프로젝트 홈페이지, 문서 사이트, 제품 페이지에 게시한다면 아래 JSON-LD를
사용할 수 있습니다. GitHub README 렌더링은 JSON-LD를 실행하지 않으므로, 실제 웹
페이지의 server-rendered `<script type="application/ld+json">` 요소 안에 배치하세요.
이 schema는 repository에서 확인 가능한 사실만 사용합니다. 공식 프로필이나 문서 URL이
추가로 생긴 뒤에만 `sameAs` 링크를 더 넣는 것이 좋습니다.

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://github.com/YooSuhwa/natural-mold#organization",
      "name": "Moldy 기여자",
      "url": "https://github.com/YooSuhwa/natural-mold",
      "sameAs": [
        "https://github.com/YooSuhwa/natural-mold"
      ],
      "description": "Moldy 기여자는 AI 에이전트를 만들고, 채팅하고, 스케줄링할 수 있는 오픈소스 self-hostable AI 에이전트 빌더를 유지보수합니다.",
      "knowsAbout": [
        "AI 에이전트 빌더",
        "LangGraph",
        "deepagents",
        "FastAPI",
        "Next.js",
        "Model Context Protocol",
        "credential 암호화",
        "에이전트 스케줄링"
      ]
    },
    {
      "@type": "SoftwareApplication",
      "@id": "https://github.com/YooSuhwa/natural-mold#software",
      "name": "Moldy",
      "url": "https://github.com/YooSuhwa/natural-mold",
      "description": "Moldy는 웹 UI에서 AI 에이전트를 만들고, 설정하고, 채팅하고, 스케줄링할 수 있는 오픈소스 self-hostable 노코드 AI 에이전트 빌더입니다.",
      "applicationCategory": "DeveloperApplication",
      "operatingSystem": "Web",
      "isAccessibleForFree": true,
      "license": "https://github.com/YooSuhwa/natural-mold/blob/main/LICENSE",
      "softwareVersion": "development snapshot, migration head m52",
      "dateModified": "2026-06-02",
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
        "대화형 AI 에이전트 빌더",
        "LangGraph 및 deepagents 런타임",
        "MCP 서버 레지스트리와 도구 가져오기",
        "Skill 패키지 관리",
        "JWT 및 HttpOnly cookie 인증",
        "Cipher V2 credential 암호화",
        "SSE 채팅 스트리밍",
        "분기 가능한 대화",
        "Cron 및 interval 에이전트 트리거",
        "Skill 마켓플레이스 설치"
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
          "name": "Moldy는 무엇을 하나요?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy는 자연어 요구사항을 도구, 스킬, MCP 도구, credential, 채팅 스트리밍, 스케줄 트리거를 사용할 수 있는 실행 가능한 AI 에이전트로 바꿉니다."
          }
        },
        {
          "@type": "Question",
          "name": "Moldy는 credential과 시스템 권한을 어떻게 보호하나요?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy는 super_user가 관리하는 시스템 리소스와 사용자별 리소스를 분리하고, JWT auth, HttpOnly cookie, CSRF 보호를 사용하며, credential payload를 HKDF-SHA256과 AES-256-GCM 기반 Cipher V2로 암호화합니다."
          }
        },
        {
          "@type": "Question",
          "name": "Moldy는 어떤 기술 스택을 사용하나요?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "Moldy는 Next.js 16, React 19, TailwindCSS v4, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, LangGraph 1.x, deepagents create_deep_agent를 사용합니다."
          }
        }
      ]
    }
  ]
}
```

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
