## M1 삭제 분석 보고서

> 분석일: 2026-04-05
> 분석 대상: SPEC.md M1 마일스톤 — 의존성 + 엔진 교체 (기본)

---

### 즉시 삭제 가능 (M1 scope)

#### 1. `create_mcp_tool()` — tool_factory.py:319-346

MCP 서버 도구를 LangChain StructuredTool로 수동 래핑하는 함수.
`langchain-mcp-adapters`의 `load_mcp_tools()`가 대체.

- **참조하는 코드:**
  - `executor.py:94` — `from app.agent_runtime.tool_factory import create_mcp_tool` (lazy import)
  - `executor.py:96-103` — `create_mcp_tool()` 호출 (type == "mcp" 분기)
- **테스트 참조:** 없음 (테스트에서 직접 import하지 않음)

#### 2. `_build_args_schema()` — tool_factory.py:298-316

JSON Schema → Pydantic 모델 변환. `create_mcp_tool()` 내부에서만 사용.

- **참조하는 코드:**
  - `tool_factory.py:339` — `create_mcp_tool()` 내부에서 호출
- **테스트 참조:** 없음
- **비고:** `create_mcp_tool()`의 내부 의존성이므로 함께 삭제

#### 3. `call_mcp_tool()` — mcp_client.py:106-120

MCP 서버에서 도구를 실행하는 함수. `langchain-mcp-adapters` 어댑터가 대체.

- **참조하는 코드:**
  - `tool_factory.py:328` — `from app.agent_runtime.mcp_client import call_mcp_tool` (lazy import)
  - `tool_factory.py:334` — `create_mcp_tool()` 내부 클로저에서 호출
- **테스트 참조:** 없음
- **비고:** `create_mcp_tool()` 제거 시 참조가 모두 사라짐

#### 4. `_extract_text()` — mcp_client.py:76-82

`call_mcp_tool()` 내부에서만 사용하는 헬퍼 함수. CallToolResult.content에서 텍스트 추출.

- **참조하는 코드:**
  - `mcp_client.py:115` — `call_mcp_tool()` 내부에서 호출
- **비고:** `call_mcp_tool()` 제거 시 더 이상 사용처 없음. 함께 삭제

#### 5. MCP 이름 가공/중복 감지 로직 — chat_service.py:201-214

중복 MCP 도구 이름에 서버 호스트를 접두사로 붙이는 로직.
`langchain-mcp-adapters`가 도구 이름 관리를 처리하므로 불필요.

- **참조하는 코드:** `build_tools_config()` 내부 로직 (외부 참조 없음)
- **영향 범위:**
  - `chat_service.py:195-198` — `mcp_server_url`, `mcp_tool_name` 설정 (type == "mcp" 분기 전체)
  - `chat_service.py:201-214` — name_counts 기반 중복 감지 + urlparse 리네이밍
- **테스트 참조:** 없음 (직접 테스트 없음)

#### 6. `create_react_agent` 폴백 로직 — executor.py:27-57

`build_agent()` 함수 전체. `create_agent` 시도 → 실패 시 `create_react_agent` 폴백.
`create_deep_agent`로 완전 교체되므로 이 함수 자체가 재작성 대상.

- **참조하는 코드:**
  - `executor.py:9` — `from langgraph.prebuilt import create_react_agent` (import)
  - `executor.py:120` — `build_agent()` 호출
- **테스트 참조:**
  - `test_executor.py:14` — `@patch("app.agent_runtime.executor.create_react_agent")`
  - `test_executor.py:30` — `@patch("app.agent_runtime.executor.create_react_agent")`
  - `test_executor.py:15-27` — `test_build_agent_calls_langgraph`
  - `test_executor.py:31-38` — `test_build_agent_returns_agent`

#### 7. executor.py MCP 도구 분기 — executor.py:93-104

`execute_agent_stream()` 내 type == "mcp" 분기. `create_mcp_tool()` lazy import + 호출.

- **참조하는 코드:** `execute_agent_stream()` 내부 (외부 참조 없음)
- **비고:** MCP 도구 생성을 `langchain-mcp-adapters`로 교체 시 이 분기 전체 재작성

---

### 삭제 시 수정 필요한 코드

| 파일:라인 | 현재 참조 | 수정 방법 |
|-----------|----------|----------|
| `executor.py:9` | `from langgraph.prebuilt import create_react_agent` | 제거 (create_deep_agent import로 교체) |
| `executor.py:18-21` | `from app.agent_runtime.tool_factory import create_builtin_tool, create_prebuilt_tool, create_tool_from_db` | `create_mcp_tool` import 제거됨 (이미 lazy import이므로 영향 없음) |
| `executor.py:27-57` | `build_agent()` 함수 전체 | `create_deep_agent()` 호출로 재작성 |
| `executor.py:93-104` | type == "mcp" 분기 | `langchain-mcp-adapters` 기반으로 재작성 |
| `executor.py:105-114` | type == "skill_package" 분기 | M3에서 제거 예정. M1에서는 유지 |
| `chat_service.py:195-198` | MCP 도구의 `mcp_server_url`, `mcp_tool_name` 설정 | MCP 도구 처리 방식 변경에 맞게 수정 (어댑터가 URL 직접 사용) |
| `chat_service.py:201-214` | MCP 이름 중복 감지 로직 | 전체 삭제 (어댑터가 처리) |
| `chat_service.py:210` | `from urllib.parse import urlparse` (lazy import) | 중복 감지 로직 삭제 시 함께 제거 |
| `test_executor.py:14-38` | `build_agent()` 테스트 2개 | `create_deep_agent` 기반으로 재작성 |
| `test_executor.py:394` | MCP 도구 config mock | 테스트 업데이트 필요 |
| `conversations.py:109` | `save_message()` 호출 | M2 scope (M1에서는 유지) |
| `conversations.py:115` | `list_messages()` 호출 | M2 scope (M1에서는 유지) |
| `conversations.py:147` | `save_message()` 호출 | M2 scope (M1에서는 유지) |
| `trigger_executor.py:43,84` | `save_message()` 호출 | M2 scope (M1에서는 유지) |

---

### 안전한 제거 순서

1. **`_build_args_schema()`** (tool_factory.py:298-316) — `create_mcp_tool()` 내부에서만 사용. 선행 제거 안전
2. **`call_mcp_tool()` + `_extract_text()`** (mcp_client.py:76-120) — `create_mcp_tool()` 내부에서만 참조. `create_mcp_tool()` 제거 전에 먼저 제거 가능
3. **`create_mcp_tool()`** (tool_factory.py:319-346) — 위 두 의존성 제거 후 안전하게 삭제
4. **executor.py type == "mcp" 분기** (executor.py:93-104) — `create_mcp_tool()` 제거 후 이 분기를 langchain-mcp-adapters로 교체
5. **chat_service.py MCP 이름 가공/중복 감지** (chat_service.py:195-214 내 MCP 관련 부분) — executor.py 교체 후 삭제
6. **`build_agent()` 함수 + create_react_agent import** (executor.py:9, 27-57) — `create_deep_agent()`로 재작성. 이것이 핵심 교체이므로 MCP 정리 후 수행

> **원칙:** 잎(leaf) → 줄기 순서로 제거. 참조가 없는 것부터 삭제하여 중간에 깨진 참조가 발생하지 않도록 함.

---

### 유지해야 하는 코드 (주의!)

| 함수/파일 | 위치 | 유지 이유 |
|-----------|------|----------|
| `test_mcp_connection()` | mcp_client.py:10-73 | MCP 서버 등록 UI에서 연결 테스트에 사용. `routers/tools.py:64`에서 import |
| `list_mcp_tools()` | mcp_client.py:85-103 | MCP 서버 도구 발견 UI에서 사용. `services/tool_service.py:59`에서 import |
| `create_builtin_tool()` | tool_factory.py:164-169 | builtin 도구 생성 (DuckDuckGo, Scraper, DateTime). Python 기반이라 MCP 대체 불가 |
| `create_prebuilt_tool()` | tool_factory.py:269-295 | prebuilt API 도구 생성 (Naver, Google). Python 기반이라 MCP 대체 불가 |
| `create_tool_from_db()` | tool_factory.py:134-150 | custom HTTP 도구 생성. 사용자 정의 도구에 필요 |
| `_build_http_tool_func()` | tool_factory.py:100-131 | `create_tool_from_db()` 내부 의존성 |
| `_BUILTIN_BUILDERS` | tool_factory.py:157-161 | `create_builtin_tool()` 내부 레지스트리 |
| `_PREBUILT_REGISTRY` | tool_factory.py:179-266 | `create_prebuilt_tool()` 내부 레지스트리 |
| `build_effective_prompt()` | chat_service.py:169-175 | M3까지 스킬 주입에 사용. M1에서는 유지 |
| `build_tools_config()` | chat_service.py:178-232 | 도구 config 빌드. MCP 관련 부분만 수정, 함수 자체는 유지 |
| `save_message()` | chat_service.py:73-104 | M2 scope. M1에서는 유지 |
| `list_messages()` | chat_service.py:61-70 | M2 scope. M1에서는 유지 |
| `model_factory.py` | 전체 | LLM 인스턴스 생성. 변경 없음 |
| `streaming.py` | 전체 | SSE 변환. 변경 없음 |
| `message_utils.py` | 전체 | 메시지 변환 유틸리티 |
| `middleware_registry.py` | 전체 | 미들웨어 빌드. create_deep_agent에 전달 |

---

### 삭제 검토 필요 (M1 scope 외)

| 항목 | 파일 | 이유 | 리스크 |
|------|------|------|--------|
| `TokenTrackingCallback` | token_tracker.py | 프로덕션 코드에서 사용하지 않음 (테스트만 존재). FR-7에서 astream() usage_metadata로 대체 예정 | 낮음 — M4에서 판단 |
| `skill_tool_factory.py` | 전체 | M3에서 `create_deep_agent(skills=[...])` 대체 예정 | M3 scope |
| `skill_executor.py` | 전체 | M3에서 deep agent FilesystemMiddleware 대체 예정 | M3 scope |
| executor.py type == "skill_package" 분기 | executor.py:105-114 | M3에서 제거 예정 | M3 scope |
| `build_effective_prompt()` 스킬 주입 | chat_service.py:169-175 | M3에서 제거 예정 | M3 scope |
| `build_tools_config()` skill_package 분기 | chat_service.py:216-230 | M3에서 제거 예정 | M3 scope |
| `creation_agent.py` | 전체 | M4에서 create_deep_agent 기반으로 교체 예정 | M4 scope |
| `fix_agent.py` | 전체 | M4에서 create_deep_agent 기반으로 교체 가능 | M4 scope, 우선순위 낮음 |
| `save_message()`, `list_messages()` | chat_service.py:61-104 | M2에서 checkpointer 전환 시 제거/교체 | M2 scope |
| `trigger_executor.py` save_message 호출 | trigger_executor.py:43,84 | M2+M4에서 deep agent invoke()로 교체 | M2/M4 scope |

---

### 단순화 제안

| 항목 | 현재 | 제안 | 마일스톤 |
|------|------|------|---------|
| MCP 도구 생성 경로 | `chat_service.build_tools_config()` → MCP config dict 생성 → `executor.py` → `create_mcp_tool()` → `call_mcp_tool()` (3단 래핑) | `executor.py` → `langchain-mcp-adapters` `load_mcp_tools()` 직접 호출 (1단) | M1 |
| `build_agent()` 함수 | try create_agent → except → create_react_agent (폴백 패턴) | `create_deep_agent()` 직접 호출 (폴백 없음) | M1 |
| `mcp_client.py` 파일 크기 | 3개 함수 (120줄) | 2개 함수 (103줄) — `call_mcp_tool()` + `_extract_text()` 제거 후 | M1 |
| `tool_factory.py` 파일 크기 | builtin + prebuilt + custom + MCP (347줄) | builtin + prebuilt + custom만 (296줄) — MCP 관련 51줄 제거 | M1 |

---

### 삭제 영향 요약

| 삭제 대상 | 프로덕션 코드 참조 | 테스트 참조 | 안전도 |
|-----------|-------------------|------------|--------|
| `_build_args_schema()` | `tool_factory.py` 내부 1곳 | 없음 | 안전 |
| `create_mcp_tool()` | `executor.py` 1곳 (lazy import) | 없음 | 안전 (executor 재작성 시) |
| `call_mcp_tool()` | `tool_factory.py` 내부 1곳 | 없음 | 안전 |
| `_extract_text()` | `mcp_client.py` 내부 1곳 | 없음 | 안전 |
| MCP 이름 가공 로직 | `chat_service.py` 내부 | 없음 | 안전 |
| `build_agent()` 재작성 | `executor.py` 내부 1곳 | `test_executor.py` 2곳 | 테스트 재작성 필요 |
| `create_react_agent` import | `executor.py:9` | `test_executor.py` 2곳 | 테스트 재작성 필요 |
