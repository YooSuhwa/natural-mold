# Moldy Architecture Map

> M1 마일스톤 기준. `create_agent` → `create_deep_agent` 엔진 교체 대상 표시.

---

## 시스템 개요

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Next.js 16)                 │
│   App Router + React 19 + TanStack Query + Jotai        │
│   SSE Client ←─────────────────────────────────────┐    │
└─────────────┬───────────────────────────────────────┘    │
              │ HTTP / SSE                                 │
              ▼                                            │
┌─────────────────────────────────────────────────────────┐│
│                  Backend (FastAPI)                       ││
│                                                         ││
│  ┌──────────┐   ┌──────────┐   ┌────────────────────┐  ││
│  │ Routers  │──▶│ Services │──▶│  Agent Runtime  ⚡  │  ││
│  │ (HTTP)   │   │ (Biz)    │   │  (LLM Execution)   │──┘│
│  └──────────┘   └──────────┘   └────────────────────┘   │
│       │              │              │                    │
│       ▼              ▼              ▼                    │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐    │
│  │ Schemas  │   │ Models   │──▶│  PostgreSQL 16   │    │
│  │(Pydantic)│   │  (ORM)   │   │  + APScheduler   │    │
│  └──────────┘   └──────────┘   └──────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

⚡ = M1 변경 대상 (Agent Runtime 레이어)

---

## 모듈 맵

### Backend (`backend/app/`)

```
app/
├── main.py                 # App factory + lifespan (시드, 스케줄러)
├── config.py               # pydantic-settings (.env)
├── database.py             # async engine + session
├── dependencies.py         # get_db, get_current_user (mock)
├── exceptions.py           # AppError + HTTP error handlers
├── scheduler.py            # APScheduler 싱글턴
│
├── routers/                # HTTP 엔드포인트
│   ├── agents.py           #   에이전트 CRUD + 미들웨어 레지스트리
│   ├── conversations.py    #   채팅/메시지 스트리밍 (핵심 진입점)
│   ├── tools.py            #   도구 CRUD + MCP 디스커버리
│   ├── skills.py           #   스킬 CRUD + 파일 서빙
│   ├── models.py           #   LLM 모델 CRUD
│   ├── templates.py        #   템플릿 CRUD
│   ├── agent_creation.py   #   대화형 에이전트 생성
│   ├── fix_agent.py        #   코드 수정 에이전트
│   ├── triggers.py         #   스케줄 트리거
│   └── usage.py            #   토큰 사용량 통계
│
├── services/               # 비즈니스 로직
│   ├── agent_service.py    #   에이전트 CRUD + 도구/스킬 연결
│   ├── chat_service.py     #   대화 관리 + 도구 구성 빌드
│   ├── tool_service.py     #   도구 CRUD
│   ├── skill_service.py    #   스킬 CRUD
│   ├── model_service.py    #   모델 CRUD
│   ├── template_service.py #   템플릿 CRUD
│   ├── agent_creation_service.py
│   ├── trigger_service.py  #   트리거 CRUD
│   └── usage_service.py    #   토큰 사용량 쿼리
│
├── agent_runtime/          # ⚡ AI 실행 엔진 (M1 핵심 변경)
│   ├── executor.py         #   ⚡ build_agent + execute_agent_stream
│   ├── model_factory.py    #     create_chat_model (provider별)
│   ├── tool_factory.py     #   ⚡ 도구 생성 (builtin/prebuilt/custom/mcp)
│   ├── mcp_client.py       #   ⚡ MCP 프로토콜 직접 구현
│   ├── middleware_registry.py #   22종 미들웨어 레지스트리
│   ├── streaming.py        #     LangGraph → SSE 변환
│   ├── message_utils.py    #     메시지 포맷 변환
│   ├── token_tracker.py    #     토큰 사용량 추적
│   ├── skill_executor.py   #     스킬 실행
│   ├── skill_tool_factory.py #   스킬 패키지 → 도구 변환
│   ├── creation_agent.py   #     에이전트 생성 메타 에이전트
│   ├── fix_agent.py        #     코드 수정 워크플로우
│   ├── trigger_executor.py #     스케줄 트리거 실행
│   ├── google_auth.py      #     Google OAuth2
│   ├── google_tools.py     #     Google 검색 도구
│   ├── google_workspace_tools.py # Gmail, Calendar, Chat
│   └── naver_tools.py      #     네이버 검색 도구
│
├── models/                 # SQLAlchemy ORM
│   ├── user.py             #   User
│   ├── agent.py            #   Agent
│   ├── conversation.py     #   Conversation + Message
│   ├── tool.py             #   Tool + AgentToolLink + MCPServer
│   ├── skill.py            #   Skill + AgentSkillLink
│   ├── model.py            #   Model (LLM 설정)
│   ├── template.py         #   Template
│   ├── token_usage.py      #   TokenUsage
│   ├── agent_creation_session.py
│   └── agent_trigger.py    #   AgentTrigger
│
├── schemas/                # Pydantic 입출력
│   ├── agent.py, conversation.py, tool.py, skill.py
│   ├── model.py, template.py, trigger.py
│   ├── token_usage.py, fix_agent.py, agent_creation.py
│   └── (models/ 테이블과 1:1 대응)
│
└── seed/                   # 시드 데이터
    ├── default_models.py   #   OpenAI, Anthropic, Google 모델
    ├── default_tools.py    #   시스템 도구 (builtin + prebuilt)
    └── default_templates.py #  에이전트 템플릿
```

---

## 의존성 방향

```
routers/ ──▶ services/ ──▶ models/
    │            │
    │            ▼
    │        schemas/
    │
    └──▶ agent_runtime/
              │
              ├──▶ model_factory   (LLM 생성)
              ├──▶ tool_factory    (도구 생성)
              ├──▶ mcp_client      (MCP 호출)
              ├──▶ middleware_registry (미들웨어)
              ├──▶ streaming       (SSE 포맷)
              └──▶ message_utils   (메시지 변환)

dependencies.py ◀── routers/ (DI: get_db, get_current_user)
config.py       ◀── agent_runtime/, services/, main.py
database.py     ◀── dependencies.py, main.py
```

**단방향 규칙:**
- `routers/` → `services/` → `models/` (역방향 없음)
- `agent_runtime/`은 `models/`, `services/`를 직접 참조하지 않음
- `services/`가 `agent_runtime/`용 config를 조립하여 `routers/`에 전달
- `routers/conversations.py`가 `executor.execute_agent_stream()`을 직접 호출 (유일한 예외)

---

## 핵심 데이터 흐름: 채팅

```
POST /api/conversations/{id}/messages
│
├─ 1. save_message(user)                          [chat_service → DB]
├─ 2. get_agent_with_tools(agent_id)              [chat_service → DB, eager-load]
├─ 3. build_effective_prompt(agent)               [chat_service, 스킬 주입]
├─ 4. build_tools_config(agent, conversation_id)  [chat_service, auth merge + 중복 해소]
│
├─ 5. execute_agent_stream(                       [executor.py]
│       provider, model_name, api_key,
│       system_prompt, tools_config,
│       messages_history, thread_id,
│       model_params, middleware_configs)
│    │
│    ├─ 5a. create_chat_model()                   [model_factory]
│    ├─ 5b. create_*_tool() × N                   [tool_factory] ⚡
│    ├─ 5c. build_middleware_instances()           [middleware_registry]
│    ├─ 5d. build_agent(model, tools, prompt, mw) [executor] ⚡
│    └─ 5e. stream_agent_response()               [streaming → SSE]
│
├─ 6. StreamingResponse → Frontend (SSE)
└─ 7. save_message(assistant) + save_token_usage  [chat_service → DB]
```

---

## M1 변경 영역 (Deep Agent 엔진 교체)

### 변경되는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `executor.py` | `build_agent()` → `create_deep_agent()` 호출로 교체. `create_react_agent` 폴백 제거 |
| `tool_factory.py` | `create_mcp_tool()`, `_build_args_schema()` 제거 |
| `mcp_client.py` | `call_mcp_tool()`, `_extract_text()` 제거. `test_mcp_connection()`, `list_mcp_tools()` 유지 |
| `chat_service.py` | MCP 도구 이름 가공/중복 감지 로직 제거 |

### 유지되는 파일

| 파일 | 이유 |
|------|------|
| `streaming.py` | SSE 포맷 변환 — `create_deep_agent`도 `CompiledStateGraph` 반환 |
| `model_factory.py` | LLM 인스턴스 생성 — 엔진 독립적 |
| `tool_factory.py` (부분) | builtin/prebuilt/custom 도구 생성 — MCP 외 유지 |
| `mcp_client.py` (부분) | `test_mcp_connection()`, `list_mcp_tools()` — UI용 |
| `middleware_registry.py` | 미들웨어 — `create_deep_agent`의 `middleware` 파라미터로 전달 |
| `trigger_executor.py` | `execute_agent_stream()` 호출 — 시그니처 유지 시 변경 불필요 |

### 새로 도입

| 컴포넌트 | 용도 |
|----------|------|
| `deepagents.create_deep_agent()` | 에이전트 생성 (model, tools, system_prompt, middleware, checkpointer) |
| `langchain_mcp_adapters.MultiServerMCPClient` | MCP 서버 연결 + `get_tools()` → LangChain 도구 변환 |

---

## 데이터 모델 관계

```
User (1) ──────┬──▶ (N) Agent
               ├──▶ (N) Tool (user-created)
               ├──▶ (N) Skill
               └──▶ (N) MCPServer

Agent (1) ─────┬──▶ (1) Model
               ├──▶ (N) AgentToolLink ──▶ (1) Tool
               ├──▶ (N) AgentSkillLink ──▶ (1) Skill
               ├──▶ (N) Conversation ──▶ (N) Message
               ├──▶ (N) AgentTrigger
               └──▶ (1) Template (optional)

Tool ──────────┬── type: builtin | prebuilt | custom | mcp
               ├── is_system: bool (시드 데이터)
               ├── auth_config: dict (도구 레벨 인증)
               └──▶ (1) MCPServer (type=mcp일 때)

Message ───────┬── role: user | assistant | tool
               └──▶ (1) TokenUsage
```

---

## 기술 스택 요약

| 레이어 | 현재 | M1 이후 | M2 이후 | M3 이후 |
|--------|------|---------|---------|---------|
| 에이전트 생성 | `create_agent` + `create_react_agent` 폴백 | `create_deep_agent` | 동일 | 동일 |
| MCP 도구 | httpx 직접 구현 (`mcp_client.call_mcp_tool`) | `langchain-mcp-adapters` (`MultiServerMCPClient`) | 동일 | 동일 |
| 대화 상태 | DB `messages` 테이블 | 동일 | LangGraph `AsyncPostgresSaver` checkpointer | 동일 |
| 스킬 | `skill_tool_factory` 도구 + 시스템 프롬프트 주입 | 동일 | 동일 | deepagents `SkillsMiddleware` (프로그레시브 디스클로저) |
| 메모리 | 없음 | 없음 | 없음 | deepagents `MemoryMiddleware` (AGENTS.md) |
| 백엔드 | 없음 | 없음 | 없음 | `FilesystemBackend` (data/, virtual_mode) |
| 미들웨어 | `langchain.agents.middleware.*` | 동일 (create_deep_agent `middleware` 파라미터) | 동일 | 동일 + SkillsMiddleware + MemoryMiddleware |
| LLM 모델 | `ChatOpenAI`, `ChatAnthropic`, `ChatGoogleGenerativeAI` | 동일 | 동일 | 동일 |
| 스트리밍 | `CompiledStateGraph.astream()` → SSE | 동일 (반환 타입 동일) | 동일 | 동일 |
| DB/ORM | SQLAlchemy 2.0 async + Alembic | 동일 | 동일 (`messages` 테이블 제거) | 동일 |
| 스케줄러 | APScheduler 3.x | 동일 | 동일 | 동일 |

---

## M2 변경 영역 (Checkpointer 전환)

> 상세 설계: [ADR-002: Checkpointer 기반 대화 관리](design-docs/adr-002-checkpointer.md)

### 아키텍처 변경 개요

```
┌─────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI)                        │
│                                                             │
│  main.py lifespan                                           │
│    ├─ init_checkpointer()  ──▶  AsyncPostgresSaver          │
│    └─ shutdown_checkpointer()                               │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────────────┐      │
│  │ Routers  │──▶│ Services │──▶│  Agent Runtime  ⚡  │      │
│  └──────────┘   └──────────┘   └────────────────────┘      │
│       │                              │                      │
│       │  GET /messages ──────────────┤                      │
│       │  → checkpointer.aget_tuple() │                      │
│       │  → message_utils 변환        │                      │
│       │                              ▼                      │
│       │                         ┌──────────────────┐        │
│       │                         │  Checkpointer    │        │
│       │                         │  (PostgreSQL)    │        │
│       │                         │  ┌────────────┐  │        │
│       │                         │  │ checkpoints│  │        │
│       │                         │  │ blobs      │  │        │
│       │                         │  │ writes     │  │        │
│       │                         │  └────────────┘  │        │
│       ▼                         └──────────────────┘        │
│  ┌──────────┐   ┌──────────┐                                │
│  │ Schemas  │   │ Models   │──▶ PostgreSQL 16               │
│  │(Pydantic)│   │  (ORM)   │   (conversations, token_usages)│
│  └──────────┘   └──────────┘   (messages 테이블 제거)        │
└─────────────────────────────────────────────────────────────┘
```

### 핵심 데이터 흐름 변경: 채팅

```
POST /api/conversations/{id}/messages
│
├─ 1. maybe_set_auto_title(content)                [chat_service → DB]
├─ 2. get_agent_with_tools(agent_id)               [chat_service → DB]
├─ 3. build_effective_prompt(agent)                 [chat_service]
├─ 4. build_tools_config(agent, conversation_id)    [chat_service]
│
├─ 5. execute_agent_stream(                         [executor.py]
│       ...,
│       messages_history=[{role: "user", content}],  ← 새 메시지만
│       thread_id=str(conversation_id),
│       ...)
│    │
│    ├─ 5a. create_chat_model()                     [model_factory]
│    ├─ 5b. create_*_tool() × N                     [tool_factory]
│    ├─ 5c. build_middleware_instances()             [middleware_registry]
│    ├─ 5d. build_agent(checkpointer=saver)         [executor → deep agent]
│    ├─ 5e. checkpointer auto-loads 이전 히스토리
│    └─ 5f. stream_agent_response()                 [streaming → SSE]
│           → checkpointer auto-saves 새 상태
│
└─ 6. StreamingResponse → Frontend (SSE)
```

**M1 대비 변경점:**
- `save_message(user)` 제거 → checkpointer 자동 저장
- `list_messages()` 제거 → checkpointer 자동 복원
- `save_message(assistant)` 제거 → checkpointer 자동 저장
- `maybe_set_auto_title()` 신규 → auto-title 로직 분리

### 변경되는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `agent_runtime/checkpointer.py` | **신규** — AsyncPostgresSaver 싱글턴 + 초기화/정리 + thread 삭제 |
| `main.py` | lifespan에서 checkpointer 초기화/정리 추가 |
| `executor.py` | `build_agent()` 호출에 `checkpointer=get_checkpointer()` 전달 |
| `routers/conversations.py` | GET/messages → checkpointer 조회, POST → save_message 제거, DELETE → delete_thread |
| `services/chat_service.py` | `save_message()`, `list_messages()` 삭제. `maybe_set_auto_title()` 추가 |
| `agent_runtime/message_utils.py` | `langchain_messages_to_response()` 추가 |
| `models/conversation.py` | `Message` 클래스 제거 |
| `models/token_usage.py` | `message_id` FK → `conversation_id` FK |

### DB 스키마 변경

| 변경 | 상세 |
|------|------|
| `messages` 테이블 | **제거** (checkpointer가 대체) |
| `token_usages.message_id` | **제거** → `conversation_id` FK 추가 |
| `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` | **자동 생성** (AsyncPostgresSaver.setup()) |

### 데이터 모델 관계 (M2 이후)

```
Agent (1) ─────┬──▶ (1) Model
               ├──▶ (N) AgentToolLink ──▶ (1) Tool
               ├──▶ (N) AgentSkillLink ──▶ (1) Skill
               ├──▶ (N) Conversation ──▶ (checkpointer: thread_id)
               ├──▶ (N) AgentTrigger
               └──▶ (1) Template (optional)

Conversation ──┬── title, is_pinned (메타데이터)
               └──▶ (N) TokenUsage (conversation_id FK)

[messages 테이블 제거 — checkpointer의 checkpoints 테이블이 대체]
```

---

## M3 변경 영역 (스킬 + 메모리 전환)

> 상세 설계: [ADR-003: 스킬 + 메모리 전환](design-docs/adr-003-skills-memory.md)

### 아키텍처 변경 개요

```
┌─────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI)                        │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────────────┐      │
│  │ Routers  │──▶│ Services │──▶│  Agent Runtime  ⚡  │      │
│  └──────────┘   └──────────┘   └────────────────────┘      │
│                      │               │                      │
│                      │               ▼                      │
│               materialize      ┌──────────────────┐        │
│               SKILL.md         │ create_deep_agent │        │
│                  │             │  + skills=[...]   │        │
│                  ▼             │  + memory=[...]   │        │
│            ┌───────────┐      │  + backend=FS     │        │
│            │ data/     │◀─────│                    │        │
│            │ ├─skills/ │      └──────────────────┘        │
│            │ │ └─{id}/ │           │         │             │
│            │ │   └─SKILL.md        │         │             │
│            │ └─agents/ │           │         │             │
│            │   └─{id}/ │     SkillsMW   MemoryMW          │
│            │     └─AGENTS.md │         │                   │
│            └───────────┘     ▼         ▼                   │
│                         시스템 프롬프트에 주입              │
└─────────────────────────────────────────────────────────────┘
```

### 핵심 데이터 흐름 변경: 채팅

```
POST /api/conversations/{id}/messages
│
├─ 1. maybe_set_auto_title(content)
├─ 2. get_agent_with_tools(agent_id)  [skill_links 포함]
├─ 3. system_prompt = agent.system_prompt  ← build_effective_prompt 제거
├─ 4. build_tools_config(agent)  ← skill_package 분기 제거
├─ 5. agent_skills = [linked skill storage_paths]
│
├─ 6. execute_agent_stream(
│       ..., agent_skills=agent_skills, agent_id=str(agent.id))
│    │
│    ├─ 6a. create_chat_model()
│    ├─ 6b. create tools (builtin/prebuilt/custom/mcp)
│    ├─ 6c. FilesystemBackend(root_dir=data/, virtual_mode=True)
│    ├─ 6d. build_agent(skills=["/skills/"], memory=["/agents/{id}/AGENTS.md"],
│    │       backend=backend, checkpointer=saver)
│    │    └─ create_deep_agent(skills, memory, backend, ...)
│    │         ├─ SkillsMiddleware → _list_skills("/skills/")
│    │         │   → 서브디렉토리 스캔 → SKILL.md 파싱
│    │         │   → 프로그레시브 디스클로저 (에이전트가 필요 시 로드)
│    │         └─ MemoryMiddleware → download AGENTS.md
│    │             → 시스템 프롬프트에 메모리 콘텐츠 주입
│    └─ 6e. stream_agent_response()
│
└─ 7. StreamingResponse → Frontend (SSE)
```

**M2 대비 변경점:**
- `build_effective_prompt()` 스킬 주입 → `SkillsMiddleware` 프로그레시브 디스클로저
- `skill_tool_factory.py` 도구 생성 → `skills` 파라미터로 대체
- `FilesystemBackend` 신규 도입 (data/ 루트)
- `MemoryMiddleware`로 에이전트 메모리 (AGENTS.md) 지원

### 변경되는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `executor.py` | `build_agent()`에 `skills`, `memory` 파라미터 추가. `execute_agent_stream()`에 `FilesystemBackend` 생성 + skills/memory 소스 구성. `skill_package` 분기 제거 |
| `services/chat_service.py` | `build_effective_prompt()` 스킬 주입 로직 제거. `build_tools_config()` skill_package 로직 제거 |
| `services/skill_service.py` | `_materialize_skill_to_disk()` 추가 (text 스킬 → SKILL.md 물질화) |
| `routers/conversations.py` | `execute_agent_stream()` 호출에 `agent_skills`, `agent_id` 전달 |

### 제거되는 파일

| 파일 | 이유 |
|------|------|
| `skill_tool_factory.py` | SkillsMiddleware가 대체 |
| `skill_executor.py` | 스크립트 실행은 에이전트 빌트인 도구로 대체 |

### 디렉토리 구조 변경

```
data/
├── skills/                  # 기존 유지 — 모든 스킬의 SKILL.md 저장소
│   └── {skill_id}/
│       ├── SKILL.md
│       ├── scripts/
│       ├── references/
│       └── _outputs/
│
├── agents/                  # ← M3 신규 — 에이전트별 메모리
│   └── {agent_id}/
│       └── AGENTS.md
│
└── conversations/           # 기존 유지
```

---

## M4 변경 영역 (최종 정리 + Creation Agent)

> 상세 설계: [ADR-004: M4 정리 — Creation Agent + Trigger + Streaming](design-docs/adr-004-m4-cleanup.md)

### 아키텍처 변경 개요

M4는 구조적 아키텍처 변경이 아닌 **코드 통합 정리**이다. 핵심 변경:

1. **creation_agent → create_deep_agent**: 마지막 비표준 LLM 호출 경로를 통합
2. **trigger_executor → direct invoke**: SSE 왕복 제거, `ainvoke()` 직접 호출
3. **executor.py 리팩터**: `_prepare_agent()` 추출로 stream/invoke 코드 공유

```
┌─────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI)                        │
│                                                             │
│  agent_runtime/executor.py                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           _prepare_agent() ← 공용 빌드             │    │
│  │   ┌───────────────────┬────────────────────┐        │    │
│  │   │ execute_agent_    │ execute_agent_      │        │    │
│  │   │ stream()          │ invoke()            │        │    │
│  │   │ (채팅 SSE)        │ (트리거 직접 실행)  │        │    │
│  │   └───────────────────┴────────────────────┘        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  agent_runtime/creation_agent.py                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ build_agent(tools=[]) → agent.ainvoke()             │    │
│  │ (checkpointer 미사용 — DB JSON 히스토리)            │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  streaming.py  → 미들웨어 JSON 필터 유지                    │
│  middleware_registry.py → PatchedLLMToolSelector 유지        │
└─────────────────────────────────────────────────────────────┘
```

### 핵심 데이터 흐름 변경: 트리거 실행

```
APScheduler → execute_trigger(trigger_id)
│
├─ 1. get_agent_with_tools(agent_id)
├─ 2. build_effective_prompt(agent)
├─ 3. build_tools_config(agent)
│
├─ 4. execute_agent_invoke(            ← NEW (execute_agent_stream 대체)
│       provider, model_name, ...,
│       messages_history=[{user: input_message}],
│       thread_id=str(conv.id))
│    │
│    ├─ 4a. _prepare_agent()            ← NEW (공용 빌드)
│    │    ├─ create_chat_model()
│    │    ├─ create tools
│    │    ├─ build middleware
│    │    ├─ FilesystemBackend
│    │    └─ build_agent(checkpointer=saver)
│    │
│    └─ 4b. agent.ainvoke()             ← SSE 우회, 직접 실행
│         → checkpointer auto-saves
│         → return final content str
│
└─ 5. Update trigger state (last_run_at, run_count)
```

**M3 대비 변경점:**
- `execute_agent_stream()` → `execute_agent_invoke()` (트리거 전용)
- SSE 인코딩/디코딩 왕복 제거
- `_prepare_agent()` 공용 함수로 에이전트 빌드 로직 단일화
- `creation_agent.py`가 `build_agent(tools=[])` + `ainvoke()` 사용

### 변경되는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `executor.py` | `_prepare_agent()` 추출, `execute_agent_invoke()` 추가 |
| `trigger_executor.py` | `execute_agent_stream()` → `execute_agent_invoke()` 호출. SSE 파싱 제거 |
| `creation_agent.py` | `model.ainvoke()` → `build_agent(tools=[])` + `agent.ainvoke()` |
| `streaming.py` | 유지 사유 주석 추가 (코드 로직 변경 없음) |
| `middleware_registry.py` | 유지 사유 주석 추가 (코드 로직 변경 없음) |

### 유지되는 파일 (조사 후 판단)

| 파일 | 유지 대상 | 조사 결과 |
|------|-----------|----------|
| `streaming.py` | `_is_tool_selector_json()` + 버퍼링 | PatchToolCallsMiddleware가 스트림 미필터링 |
| `middleware_registry.py` | `PatchedLLMToolSelectorMiddleware` | GPT-4o `{"const"}` 이슈 deepagents 미처리 |

### 기술 스택 요약 (M4 이후 최종)

| 레이어 | M3 | M4 이후 |
|--------|-----|---------|
| 에이전트 생성 | `create_deep_agent` | 동일 (creation_agent도 통합) |
| 실행 경로 | `astream()` only | `astream()` (채팅) + `ainvoke()` (트리거) |
| SSE | 채팅 + 트리거 | 채팅만 |
| Creation Agent | `model.ainvoke()` 직접 | `create_deep_agent(tools=[])` + `ainvoke()` |
| 스트리밍 필터 | 미들웨어 JSON 필터 유지 | 동일 (유지 판단 완료) |

---

## v2 변경 영역 (Builder + Assistant 교체)

> 상세 설계: [ADR-005: Builder/Assistant 아키텍처](design-docs/adr-005-builder-assistant.md)
> 기획서: [moldy-agent-builder-spec-v2.md](moldy-agent-builder-spec-v2.md)

### 아키텍처 변경 개요

기존 `creation_agent.py` (단일 GPT-4o, 4단계 대화형)와 `fix_agent.py` (단일 LLM, JSON changes)를
**Builder** (7단계 파이프라인 오케스트레이터 + 4 서브에이전트)와 **Assistant** (32개 도구 기반 에이전트)로 교체한다.

```
┌─────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI)                        │
│                                                             │
│  agent_runtime/builder/                                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  orchestrator.py  ← LangGraph StateGraph            │    │
│  │  ┌──────┐ ┌──────┐ ┌────────┐ ┌────────┐           │    │
│  │  │Intent│→│Tools │→│Middlew.│→│Prompt  │           │    │
│  │  │Agent │ │Agent │ │Agent   │ │Agent   │           │    │
│  │  └──────┘ └──────┘ └────────┘ └────────┘           │    │
│  │      Phase 2    3        4         5                │    │
│  │                                                     │    │
│  │  Phase 1 (init) → ... → Phase 6 (config)           │    │
│  │                            → Phase 7 (build_final)  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  agent_runtime/assistant/                                   │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  assistant.py  ← create_deep_agent + 32 tools       │    │
│  │  tools.py      ← Assistant 도구 팩토리               │    │
│  │  (VERIFY-MODIFY 루프, 시스템 프롬프트 기반)           │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  routers/                                                   │
│  ├── builder.py           # POST /start, SSE /stream,      │
│  │                        # POST /confirm                   │
│  └── assistant.py         # POST /message (SSE),            │
│                           # GET /config                     │
│                                                             │
│  services/                                                  │
│  ├── builder_service.py   # 빌드 세션 관리 + DB 연동        │
│  └── assistant_service.py # Assistant 세션 + 도구 실행       │
└─────────────────────────────────────────────────────────────┘
```

### 디렉토리 구조 (신규)

```
agent_runtime/
├── builder/
│   ├── __init__.py
│   ├── orchestrator.py        # LangGraph StateGraph 7-phase 파이프라인
│   ├── state.py               # BuilderState TypedDict
│   ├── sub_agents.py          # 4 서브에이전트 생성 (intent/tools/middleware/prompt)
│   ├── prompts.py             # 서브에이전트 시스템 프롬프트
│   └── tools.py               # Builder 전용 도구 (write_*, create_*, build_final)
│
├── assistant/
│   ├── __init__.py
│   ├── assistant.py           # create_deep_agent + 도구 바인딩
│   ├── tools.py               # 32개 도구 팩토리 (read/write/clarify)
│   └── prompt.py              # Assistant 시스템 프롬프트 로더
│
├── executor.py                # 기존 유지 (채팅 실행)
├── model_factory.py           # 기존 유지
├── tool_factory.py            # 기존 유지
├── middleware_registry.py     # 기존 유지
├── streaming.py               # 기존 유지 + Builder SSE 재사용
└── ...
```

### 교체 대상 → 신규 매핑

| 기존 (삭제 대상) | 신규 (교체) | 비고 |
|------------------|-------------|------|
| `creation_agent.py` | `builder/orchestrator.py` | 단일 LLM → 오케스트레이터 + 4 서브에이전트 |
| `fix_agent.py` | `assistant/assistant.py` | JSON changes → 32개 도구 기반 |
| `routers/agent_creation.py` | `routers/builder.py` | SSE 스트리밍 추가 |
| `routers/fix_agent.py` | `routers/assistant.py` | SSE 스트리밍 추가 |
| `services/agent_creation_service.py` | `services/builder_service.py` | 세션 + 빌드 결과 관리 |
| `schemas/agent_creation.py` | `schemas/builder.py` | BuilderState, Intent 등 |
| `schemas/fix_agent.py` | `schemas/assistant.py` | AssistantMessage 등 |
| `models/agent_creation_session.py` | `models/builder_session.py` | 상태 필드 확장 |

### 핵심 데이터 흐름: Builder

```
POST /api/builder/start
│
├─ 1. create_builder_session(user_id, request)     [builder_service → DB]
│      → BuilderSession(status=building, user_request=...)
│
├─ 2. SSE: GET /api/builder/{session_id}/stream
│      → orchestrator.astream(BuilderState)
│    │
│    ├─ Phase 1: init (도구 직접 호출)
│    │   └─ SSE event: phase_progress {phase: 1, status: "completed"}
│    │
│    ├─ Phase 2: intent_agent.ainvoke(description)
│    │   └─ SSE event: phase_progress {phase: 2, result: AgentCreationIntent}
│    │
│    ├─ Phase 3: tool_agent.ainvoke(intent)
│    │   └─ SSE event: phase_progress {phase: 3, result: tools[]}
│    │
│    ├─ Phase 4: middleware_agent.ainvoke(intent + tools)
│    │   └─ SSE event: phase_progress {phase: 4, result: middlewares[]}
│    │
│    ├─ Phase 5: prompt_agent.ainvoke(intent + tools + mw)
│    │   └─ SSE event: phase_progress {phase: 5, result: system_prompt}
│    │
│    ├─ Phase 6: write_config (도구 직접 호출)
│    │   └─ SSE event: phase_progress {phase: 6, status: "completed"}
│    │
│    └─ Phase 7: build_preview → SSE event: build_preview {draft_config}
│
├─ 3. Frontend: 사용자에게 draft_config 프리뷰 표시
│
└─ 4. POST /api/builder/{session_id}/confirm
       → build_final_agent() → Agent 생성 + DB 저장
       → AgentResponse 반환
```

### 핵심 데이터 흐름: Assistant

```
POST /api/agents/{agent_id}/assistant/message
│
├─ 1. get_or_create_assistant_session(agent_id)    [assistant_service]
├─ 2. get_agent_with_tools(agent_id)               [agent_service]
│
├─ 3. SSE: assistant.astream({messages})
│    │
│    ├─ 도구 호출: get_agent_config → 현재 에이전트 설정 조회
│    ├─ 도구 호출: list_available_tools → 사용 가능 도구 목록
│    ├─ 도구 호출: add_tool_to_agent → 도구 추가 (DB 반영)
│    ├─ 도구 호출: edit_system_prompt → 프롬프트 부분 수정
│    └─ ... (32개 도구 중 필요한 것만 호출)
│
├─ 4. SSE events:
│    ├─ content_delta  → 텍스트 스트리밍
│    ├─ tool_call_start → 도구 호출 시작 (UI에 진행 표시)
│    └─ message_end    → 완료
│
└─ 5. 변경사항은 도구가 직접 DB에 반영 (별도 confirm 불필요)
```

### Builder SSE 이벤트 타입

| 이벤트 | 데이터 | 설명 |
|--------|--------|------|
| `phase_progress` | `{phase, status, message}` | 단계 진행 상황 |
| `sub_agent_start` | `{phase, agent_name}` | 서브에이전트 실행 시작 |
| `sub_agent_token` | `{phase, delta}` | 서브에이전트 토큰 스트리밍 (선택) |
| `sub_agent_end` | `{phase, result_summary}` | 서브에이전트 실행 완료 |
| `build_preview` | `{draft_config}` | 최종 빌드 프리뷰 |
| `build_complete` | `{agent_id}` | 빌드 완료 |
| `error` | `{phase, message}` | 오류 발생 |

### 재사용 코드 (변경 불필요)

| 모듈 | Builder에서 | Assistant에서 |
|------|-------------|---------------|
| `model_factory.py` | 서브에이전트 LLM 생성 | Assistant LLM 생성 |
| `tool_factory.py` | Phase 7 `build_final` | - |
| `middleware_registry.py` | Phase 4 카탈로그 조회 | 미들웨어 목록 도구 |
| `executor.py` | Phase 7 `build_agent()` 호출 | - |
| `streaming.py` | `format_sse()` 재사용 | 채팅 SSE 재사용 |
| `message_utils.py` | 메시지 변환 | 메시지 변환 |
| `checkpointer.py` | - | Assistant 대화 히스토리 |

### DB 스키마 변경

| 변경 | 상세 |
|------|------|
| `agent_creation_sessions` | **확장** → `builder_sessions` (phase 추적, intent/tools/mw/prompt 중간 결과) |
| `agents` | 변경 없음 (Builder가 최종적으로 Agent 레코드 생성) |

### 데이터 모델 관계 (v2 이후)

```
User (1) ──────┬──▶ (N) Agent
               ├──▶ (N) BuilderSession
               │         ├── user_request: str
               │         ├── current_phase: int
               │         ├── intent: JSON (AgentCreationIntent)
               │         ├── tools_result: JSON
               │         ├── middlewares_result: JSON
               │         ├── system_prompt: text
               │         ├── draft_config: JSON
               │         └── status: building|preview|completed|failed
               │
               └──▶ Agent ◀── BuilderSession.agent_id (완료 후 연결)
                     │
                     └──▶ AssistantSession (via conversation)
                           ├── checkpointer 기반 히스토리
                           └── 32개 도구로 에이전트 설정 직접 수정
```
