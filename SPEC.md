# Deep Agent 엔진 전면 교체 명세서

## 개요

natural-mold(Moldy) 프로젝트의 AI 에이전트 실행 엔진을 `langchain.agents.create_agent`에서 `deepagents.create_deep_agent`로 교체한다. 동시에 MCP 직접 구현을 `langchain-mcp-adapters`로 대체하고, 대화 관리를 LangGraph checkpointer로 전환한다.

## 목표

- [ ] create_agent → create_deep_agent 교체
- [ ] MCP 직접 구현 → langchain-mcp-adapters 교체
- [ ] DB 메시지 관리 → LangGraph PostgresSaver checkpointer 교체
- [ ] 스킬 수동 로딩 → deep agent skills 파라미터로 자동 로딩
- [ ] 자체 구현 코드 최대한 제거 (표준 프레임워크 활용)
- [ ] 기존 DB 데이터 전체 초기화 (깨끗한 시작)

## 기술 스택 변경

| 항목 | Before | After |
|------|--------|-------|
| 에이전트 엔진 | `langchain.agents.create_agent` | `deepagents.create_deep_agent` |
| MCP 클라이언트 | httpx 직접 구현 (`mcp_client.py`) | `langchain-mcp-adapters` |
| MCP→도구 변환 | `create_mcp_tool()` 수동 래핑 | `load_mcp_tools()` 자동 변환 |
| 대화 상태 | DB messages 테이블 | LangGraph `PostgresSaver` |
| 스킬 로딩 | `skill_tool_factory.py` 수동 | `create_deep_agent(skills=[...])` 자동 |
| 스킬 실행 | `skill_executor.py` 수동 | deep agent `FilesystemMiddleware` |
| 메모리 | 없음 | deep agent memory (AGENTS.md 파일 기반) |
| 자동 요약 | 없음 (미들웨어로 선택) | deep agent 내장 `SummarizationMiddleware` |

## 결정 사항 (인터뷰 결과)

| 항목 | 결정 |
|------|------|
| 대화 저장 | Checkpointer 전면 사용. conversations 테이블은 메타데이터(title, pinned)만 유지. messages 테이블 제거 |
| 자동 추가 도구 | 기본 활성화 + 시스템 프롬프트로 불필요한 호출 제어 |
| 토큰 추적 | 대화(thread) 단위로 변경. message_id FK 제거 |
| 트리거/스케줄러 | APScheduler 유지. trigger_executor.py만 deep agent invoke()로 수정 |
| 메모리 저장 | 파일 기반 (data/agents/{agent_id}/AGENTS.md). deep agent 기본 방식 |
| UI 설정 | 최소한. deep agent 기능은 백엔드 자동 처리. 기존 미들웨어 설정 UI 유지 |
| Creation agent | deep agent로 교체 |
| Checkpointer DB | 현재 PostgreSQL과 같은 인스턴스 |
| Builtin/prebuilt 도구 | tool_factory.py 유지. MCP 코드만 제거 |
| 구현 순서 | 단계별 검증 (M1→M2→M3→M4) |

## 상세 요구사항

### 기능적 요구사항

#### FR-1: create_deep_agent 엔진 교체
- `executor.py`의 `build_agent()` 함수를 `create_deep_agent()`로 교체
- 반환 타입 동일 (CompiledStateGraph) → `astream()` 호출 변경 불필요
- 기존 middleware 파라미터 그대로 전달 (langchain 공식 22종 호환)
- `create_react_agent` 폴백 로직 제거

#### FR-2: langchain-mcp-adapters 도입
- MCP 도구 생성을 `load_mcp_tools()` 또는 `MultiServerMCPClient`로 교체
- 도구 이름, description, parameters_schema가 MCP 서버 원본 그대로 전달
- auth_config → HTTP headers로 변환하여 전달
- 에이전트에 연결된 특정 도구만 필터링 (tool_name 기준)

#### FR-3: Checkpointer 기반 대화 관리
- `PostgresSaver`를 checkpointer로 사용 (현재 PostgreSQL 인스턴스)
- conversation_id를 thread_id로 사용
- 프론트엔드가 메시지를 가져오는 API 변경:
  - 현재: `GET /api/conversations/{id}/messages` → DB 쿼리
  - 변경: checkpointer에서 state를 가져와 messages 추출
- conversations 테이블: title, is_pinned, agent_id 등 메타데이터만 유지
- messages 테이블: 제거

#### FR-4: 스킬 자동 로딩
- `create_deep_agent(skills=[skill_dir1, skill_dir2, ...])` 파라미터로 전달
- DB의 agent_skills에서 skill.storage_path 목록을 수집하여 전달
- skill_tool_factory.py, skill_executor.py 제거
- SKILL.md의 progressive disclosure가 deep agent에 의해 자동 처리

#### FR-5: 메모리 (장기 기억)
- 에이전트별 `data/agents/{agent_id}/AGENTS.md` 파일 기반
- deep agent의 memory 파라미터로 전달
- 에이전트가 대화 간 학습 내용을 자동 기억

#### FR-6: 자동 추가 도구 관리
- deep agent가 자동 추가하는 도구: ls, read_file, write_file, edit_file, glob, grep, write_todos
- 모든 에이전트에 기본 활성화
- 시스템 프롬프트에 도구 가이드를 포함하여 불필요한 호출 방지
- LLMToolSelectorMiddleware가 도구 선택 필터링 지원

#### FR-7: 토큰 사용량 추적
- token_usages 테이블 유지
- message_id FK → conversation_id + 타임스탬프 기반으로 변경
- astream()의 usage_metadata에서 실시간 추출

#### FR-8: 트리거 연동
- APScheduler 유지
- trigger_executor.py에서 `create_deep_agent` → `invoke()` 호출로 변경
- 트리거 실행 결과도 checkpointer에 자동 저장

#### FR-9: Creation Agent 교체
- creation_agent.py를 create_deep_agent 기반으로 교체
- 통일된 엔진

### 비기능적 요구사항

#### NFR-1: 성능
- MCP 도구 로딩: 세션 재사용으로 연결 오버헤드 최소화
- checkpointer: PostgreSQL async 연결 사용
- 자동 요약: 컨텍스트 윈도우 85% 도달 시 자동 압축

#### NFR-2: 호환성
- 프론트엔드: SSE 이벤트 포맷(message_start, content_delta, tool_call_start, tool_call_result, message_end) 유지
- REST API: 엔드포인트 구조 유지 (/api/agents/*, /api/conversations/*)
- 미들웨어: langchain 공식 22종 그대로 사용

#### NFR-3: 보안
- MCP auth_config 암호화 유지 (기존 ENCRYPTION_KEY)
- deep agent FilesystemMiddleware: 스킬 디렉토리만 접근 가능하도록 제한
- execute 도구: 스킬 스크립트만 실행 가능 (샌드박스 제한)

## DB 스키마 변경

### 유지하는 테이블
- users
- models
- templates
- mcp_servers
- tools
- agents (middleware_configs, model_params 구조 유지)
- agent_tools (config JSON 포함)
- skills
- agent_skills
- agent_triggers
- agent_creation_sessions

### 변경하는 테이블

#### token_usages — FK 변경
```sql
-- Before
message_id UUID FK → messages.id

-- After
conversation_id UUID FK → conversations.id  (message_id 제거)
```

### 제거하는 테이블
- messages (checkpointer가 대체)

### 자동 생성 테이블 (PostgresSaver)
- checkpoint (LangGraph가 자동 생성)
- checkpoint_blobs
- checkpoint_writes

## 제거하는 코드

| 파일 | 제거 대상 | 이유 |
|------|---------|------|
| `tool_factory.py` | `create_mcp_tool()`, `_build_args_schema()` | langchain-mcp-adapters가 대체 |
| `mcp_client.py` | `call_mcp_tool()` | 어댑터가 도구 실행 처리 |
| `skill_tool_factory.py` | 전체 파일 | create_deep_agent가 스킬 로딩 처리 |
| `skill_executor.py` | 전체 파일 | deep agent FilesystemMiddleware가 대체 |
| `chat_service.py` | `build_effective_prompt()` 스킬 주입 부분, MCP 이름 가공, 중복 감지 | deep agent/어댑터가 처리 |
| `chat_service.py` | `save_message()`, `list_messages()` | checkpointer가 대체 |
| `streaming.py` | 미들웨어 JSON 필터 (선택적) | deep agent 전환 후 테스트하여 판단 |
| `middleware_registry.py` | `PatchedLLMToolSelectorMiddleware` (선택적) | deep agent 전환 후 테스트하여 판단 |
| `executor.py` | `create_react_agent` 폴백 로직 | 항상 create_deep_agent 사용 |

## 유지하는 코드

| 파일 | 유지 대상 | 이유 |
|------|---------|------|
| `mcp_client.py` | `test_mcp_connection()`, `list_mcp_tools()` | MCP 서버 등록 UI에서 사용 |
| `tool_factory.py` | builtin/prebuilt/custom 도구 생성 | Python 코드 기반이라 MCP 교체 불가 |
| `model_factory.py` | 전체 | 모델 생성 방식 동일 |
| `message_utils.py` | 전체 | checkpointer에서 가져온 메시지 변환에 사용 |
| `streaming.py` | SSE 변환 로직 | astream() API 동일, SSE 포맷 유지 |
| `middleware_registry.py` | 미들웨어 레지스트리, build_middleware_instances | deep agent middleware 파라미터로 전달 |

## API 변경

### 변경 없음
- `GET /api/agents` — 에이전트 목록
- `POST /api/agents` — 에이전트 생성
- `GET /api/agents/{id}` — 에이전트 상세
- `PUT /api/agents/{id}` — 에이전트 수정
- `GET /api/agents/{id}/conversations` — 대화 목록
- `POST /api/agents/{id}/conversations` — 대화 생성
- `POST /api/conversations/{id}/messages` — 메시지 전송 (SSE 스트리밍)
- `PATCH /api/conversations/{id}` — 대화 수정
- `DELETE /api/conversations/{id}` — 대화 삭제
- 도구 관련 API 전체

### 내부 구현만 변경
- `GET /api/conversations/{id}/messages` — DB 쿼리 → checkpointer에서 state 추출
- `POST /api/conversations/{id}/messages` — execute_agent_stream() → deep agent astream()

## 마일스톤

### M1: 의존성 + 엔진 교체 (기본)
- [ ] `uv add deepagents langchain-mcp-adapters`
- [ ] `executor.py`: `build_agent()` → `create_deep_agent()` 교체
- [ ] `executor.py`: MCP 도구 생성을 `langchain-mcp-adapters`로 교체
- [ ] `tool_factory.py`: `create_mcp_tool()`, `_build_args_schema()` 제거
- [ ] `mcp_client.py`: `call_mcp_tool()` 제거
- [ ] `chat_service.py`: MCP 이름 가공/중복 감지 로직 제거
- [ ] 검증: 기존 에이전트가 MCP 도구를 정상 호출하는지 테스트
- [ ] 검증: builtin/prebuilt 도구가 정상 동작하는지 테스트

### M2: Checkpointer 전환
- [ ] `PostgresSaver` 설정 (현재 PostgreSQL 연결 재사용)
- [ ] `create_deep_agent(checkpointer=saver)` 전달
- [ ] `conversations.py`: 메시지 조회 API를 checkpointer에서 state 추출로 변경
- [ ] `conversations.py`: 메시지 전송 API에서 수동 save_message() 제거
- [ ] `chat_service.py`: `save_message()`, `list_messages()` 제거 또는 checkpointer 래퍼로 교체
- [ ] DB 마이그레이션: messages 테이블 제거, token_usages FK 변경
- [ ] 검증: 대화 생성 → 메시지 전송 → 히스토리 조회 전체 흐름 테스트

### M3: 스킬 + 메모리 전환
- [ ] `create_deep_agent(skills=[...])` 파라미터로 스킬 디렉토리 전달
- [ ] `skill_tool_factory.py`, `skill_executor.py` 제거
- [ ] `chat_service.py`: `build_effective_prompt()`에서 스킬 주입 로직 제거
- [ ] `create_deep_agent(memory=[...])` 파라미터로 메모리 경로 전달
- [ ] `data/agents/{agent_id}/` 디렉토리 구조 설정
- [ ] 검증: "이상윤 자리 어디야?" → 스킬 자동 로딩 → mark_seat.py 실행 + 이미지

### M4: 정리 + Creation Agent
- [ ] `creation_agent.py`를 create_deep_agent 기반으로 교체
- [ ] `trigger_executor.py`를 deep agent invoke()로 교체
- [ ] 불필요한 코드/파일 최종 정리
- [ ] streaming.py 미들웨어 필터가 여전히 필요한지 테스트 후 판단
- [ ] middleware_registry.py 패치가 여전히 필요한지 테스트 후 판단
- [ ] 전체 E2E 테스트
- [ ] DB 시드 데이터 정리

## 열린 질문 / 결정 필요

1. **deep agent의 FilesystemMiddleware가 스킬 디렉토리를 어떻게 제한하는지** — 보안상 전체 파일시스템 접근을 막아야 함. backend 설정으로 제어 가능한지 실제 코드 확인 필요.

2. **streaming.py의 미들웨어 JSON 필터가 deep agent에서도 필요한지** — create_deep_agent가 내장 PatchToolCallsMiddleware를 포함하므로, 기존 content leak 문제가 해결됐을 수 있음. M4에서 테스트.

3. **auto-added SummarizationMiddleware와 사용자 설정 summarization 중복** — 사용자가 에이전트 설정에서 summarization 미들웨어를 별도로 켜면 deep agent 내장 + 사용자 설정이 이중 적용됨. 중복 방지 로직 필요.

4. **checkpointer에서 메시지를 가져오는 정확한 API** — `PostgresSaver.aget_tuple(config)` 등으로 state를 가져와 `state["messages"]`를 추출하는 방식의 정확한 구현 확인 필요.

---

> 명세서가 완성되었습니다. 새 세션에서 다음 명령어로 구현을 시작하세요:
> ```
> SPEC.md 읽고 구현 시작해줘
> ```
>
> 구현 완료 후 검증:
> ```
> /spec-verify
> ```
