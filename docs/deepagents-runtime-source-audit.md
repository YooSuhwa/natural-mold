# Moldy DeepAgents 런타임 소스 기반 개선 감사

작성일: 2026-05-30
대상: `natural-mold` 현재 소스 코드, 설치된 `deepagents==0.6.1`, 첨부 문서 4개

검토한 첨부 문서:

- `/Users/chester/Downloads/deepagents-runtime-audit.md`
- `/Users/chester/Downloads/fleet-vs-moldy-analysis.md`
- `docs/design-docs/hitl-ask-user-standardization-plan.md`
- `docs/design-docs/langfuse-trace-debugger-plan.md`

검토 기준:

- 로컬 LangChain/Deep Agents 스킬 문서
  - `framework-selection`
  - `deep-agents-core`
  - `deep-agents-memory`
  - `deep-agents-orchestration`
  - `langgraph-human-in-the-loop`
- 현재 설치된 DeepAgents API
  - `create_deep_agent()` 시그니처: `subagents`, `skills`, `memory`, `permissions`, `backend`, `interrupt_on`, `store`, `checkpointer` 지원 확인
  - `FilesystemBackend`는 `SandboxBackendProtocol`이 아님 확인
  - `FilesystemPermission`: `operations`, `paths`, `mode` 구조 확인

## 1. 결론 요약

Moldy가 DeepAgents를 선택한 방향 자체는 맞다. 제품은 no-code agent builder이고, 장기 작업, 스킬, 파일, 스케줄, MCP, HITL, subagent delegation이 모두 필요하다. 이는 LangChain 단일 `create_agent`보다 DeepAgents 레이어에 더 잘 맞는다.

문제는 DeepAgents를 "호출"하고는 있지만, 제품 설정 모델과 DeepAgents harness가 아직 완전히 연결되지 않은 부분이 많다는 점이다. 특히 DB/UI에는 있는 sub-agent가 실제 `create_deep_agent(subagents=...)`로 들어가지 않고, 파일 권한은 `permissions` 없이 전역 `backend/data`를 열고 있으며, skill script 실행은 DeepAgents sandbox 모델을 우회해 서버 프로세스 권한으로 실행된다.

첨부 문서 중 일부 주장은 이미 현재 코드에서 개선되었다. 대표적으로 broad `/skills/` mount와 schedule run history 없음은 현재 소스 기준으로 상당 부분 해소되어 있다. 하지만 core runtime 쪽의 중요한 문제는 여전히 남아 있고, 몇 가지는 첨부 문서보다 더 명확하게 위험해졌다.

가장 먼저 고칠 항목은 "새 기능을 켜는 것"보다 "권한/credential/승인 경계를 먼저 세우는 것"이다. sub-agent runtime 연결은 핵심 기능이지만, 현재 file permission과 HITL이 약한 상태에서 먼저 켜면 위험한 tool surface가 child agent까지 넓어진다.

재정렬된 최우선 순서:

1. 기존 `/api/conversations/{conversation_id}/traces`에 auth/ownership guard를 추가한다.
2. user agent middleware model이 system/env credential을 쓰지 못하게 막는다.
3. `ask_user`와 승인 HiTL을 DeepAgents top-level `interrupt_on` 표준 경로로 통일한다.
4. tool risk policy를 만들고 trigger mode에서 위험 도구를 기본 차단한다.
5. `FilesystemBackend(root_dir=data)`에 DeepAgents `permissions`를 붙이거나 `CompositeBackend`로 격리한다.
6. `execute_in_skill`을 우선 HITL/deny-by-default로 묶고, 이어 sandbox/worker 기반으로 바꾼다.
7. MCP runtime에서 discovery와 동일한 credential interpolation/transport 지원을 보장한다.
8. 위 안전 경계가 선 뒤 저장된 sub-agent를 실제 DeepAgents `subagents` 구성으로 전달한다.
9. `stream_mode="messages"` 기반 SSE를 DeepAgents event stream/tool_call_id 중심으로 확장한다.
10. streaming error가 hook/trace/message_events에서 성공처럼 기록되지 않도록 실패 상태를 전파한다.
11. 그 다음 Langfuse trace debugger POC를 붙여 내부 trace를 외부 span waterfall로 보강한다.

## 2. 첨부 문서 주장 검증

### 2.1 `deepagents-runtime-audit.md` 검증

| 첨부 ID | 현재 판단 | 근거 |
|---|---|---|
| DA-01/02: 저장된 sub-agent가 runtime에 전달되지 않음 | 유효 | `Agent.sub_agent_links`와 API 저장 경로는 있으나 `AgentConfig`/`build_agent()`/`create_deep_agent()` 호출에 `subagents`가 없다. |
| DA-03: skill slug/UUID path mismatch | 대부분 해소 | 현재는 per-thread `/runtime/{thread_id}/skills/{slug}` copytree와 prompt prefix rewrite가 있다. 다만 canonical `/skills/<uuid>`는 여전히 전역 backend 아래에 존재한다. |
| DA-04: selected skill이 아니라 `/skills/` 전체 mount | 부분 해소 | `skills_sources = ["/runtime/{thread_id}/skills/"]`로 바뀌었다. 그러나 Filesystem tool 권한이 없어서 모델이 `/skills/<uuid>` 등 data root 내 다른 경로를 시도할 수 있다. |
| DA-05: `execute_in_skill`이 sandbox가 아니라 host subprocess | 유효, 더 심각 | 현재 Python뿐 아니라 `curl`도 허용하고, credential env를 주입한다. OS sandbox/chroot/network 제한이 없다. |
| DA-06: 전역 `FilesystemBackend`, `permissions` 미사용 | 유효 | `FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)`만 사용하고 `permissions` 파라미터는 wrapper에 없다. |
| DA-07: memory가 Store/CompositeBackend 기반이 아님 | 부분 유효 | `/agents/{agent_id}/AGENTS.md` file memory는 사용하지만 StoreBackend/namespace/approval/UI는 없다. |
| DA-08: HITL이 built-in file tools를 놓침 | 유효, 더 심각 | auto `interrupt_on` 계산이 skill tool/ask_user 추가보다 먼저 실행되고, DeepAgents built-in `write_file`/`edit_file`은 계산 대상에 없다. |
| DA-09: trigger mode에서 HITL 강제 off | 유효 | hang 방지 목적은 맞지만 schedule/channel용 risk policy/approval queue가 없다. |
| DA-10/11: streaming 구조와 tool result 매칭 취약 | 유효 | `stream_mode="messages"`만 사용하며 SSE에 `tool_call_id`가 없다. frontend는 일반 result를 마지막 tool call에 붙인다. |
| DA-12: assistant fixer도 DeepAgents built-in tools를 받음 | 유효 | assistant도 `build_agent()`를 사용하므로 DeepAgents built-in tool suite가 additive로 들어간다. |
| DA-13: middleware catalog와 runtime filtering 불일치 | 유효 | public API는 auto-injected를 숨기지만 assistant read tool은 전체 registry를 보여준다. |
| DA-14: provider middleware 중복 가능성 | 유효 | DeepAgents 0.6.1은 AnthropicPromptCachingMiddleware를 tail stack에 무조건 추가한다. Moldy도 anthropic provider에 직접 추가한다. |

### 2.2 `fleet-vs-moldy-analysis.md` 검증

첨부 Fleet 비교 문서는 방향성은 좋지만, schedule 관련 일부 내용은 현재 코드보다 오래되었다.

이미 보완된 부분:

- `agent_trigger_runs` 모델이 있고 run history API가 있다.
  - `backend/app/models/agent_trigger_run.py`
  - `backend/app/services/trigger_service.py:368-493`
  - `backend/app/routers/triggers.py:98-107`
- schedule conversation policy가 있다.
  - `schedule_thread`, `new_per_run`, `selected_conversation`
  - `backend/app/services/trigger_service.py:18-22`, `388-424`
- `one_time` trigger가 scheduler까지 연결되어 있다.
  - `backend/app/scheduler.py:100-119`
- frontend `/schedules`에 history dialog가 있다.
  - `frontend/src/app/schedules/page.tsx:411-469`

여전히 유효한 Fleet gap:

- Channels는 placeholder다.
  - `frontend/src/components/agent/visual-settings/nodes/channels-node.tsx:7-23`
- Agent identity, fixed/user credential policy, channel delivery target, async approval inbox는 없다.
- MCP는 discovery와 runtime의 credential/transport 처리 차이가 남아 있다.
- LangSmith Fleet 수준의 run/trace/eval/replay는 아직 부분 구현이다.

문서 자체도 갱신 필요:

- `AGENTS.md`는 "M39 최신"이라고 설명하지만 현재 소스에는 `m50_schedule_run_metadata`까지 있다.
- `docs/PRD.md`는 아직 PoC/mock auth 서술이 많이 남아 있어 ADR-016 이후 코드 상태와 맞지 않는다.
- `docs/marketplace-resources-prd.md`와 spec에는 broad `/skills/` mount 과거 상태가 남아 있으나 현재는 per-thread runtime root로 바뀌었다.

### 2.3 `hitl-ask-user-standardization-plan.md` 검증

이 문서는 현재 소스와 잘 맞는다. 특히 P0-4의 root cause를 더 정확하게 설명한다.

유효한 주장:

- `ask_user` tool이 `interrupt_on` 계산 이후에 추가된다.
  - wrap 시도: `backend/app/agent_runtime/executor.py:703-706`
  - 실제 tool 추가: `backend/app/agent_runtime/executor.py:773-776`
- `ask_user` 표준 정책은 `interrupt_on is not None`일 때만 merge된다. 즉 explicit HITL 설정이 없으면 `ask_user`가 표준 `respond` decision으로 감싸지지 않는다.
  - `backend/app/agent_runtime/executor.py:703-706`
- `HumanInTheLoopMiddleware`를 직접 middleware list에 넣고 `create_deep_agent(interrupt_on=None)`으로 호출한다.
  - 직접 주입: `backend/app/agent_runtime/executor.py:715-716`
  - DeepAgents top-level path 비활성화: `backend/app/agent_runtime/executor.py:786-789`
- DeepAgents 0.6.1은 top-level `interrupt_on`을 declarative subagent와 기본 `general-purpose` subagent에 상속한다.
  - `deepagents/graph.py:591-609`
  - `deepagents/graph.py:665-666`
- native `ask_user.py`는 `interrupt()` resume 값을 그대로 `str(response)`로 반환한다.
  - `backend/app/agent_runtime/tools/ask_user.py:29-36`
  - resume router는 항상 `{"decisions": [...]}` 형태를 보낸다.
  - `backend/app/routers/conversations.py:843-856`
- native ask_user fallback adapter는 `review_configs[].action_name`이 아니라 `tool_name`을 사용한다.
  - `backend/app/agent_runtime/streaming.py:111-116`
- frontend `useChatRuntime`은 `onStandardInterrupt` callback을 지원하지만 일반 대화 페이지는 이를 넘기지 않는다.
  - callback 호출: `frontend/src/lib/chat/use-chat-runtime.ts:446-458`
  - 일반 대화 페이지: `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx:120-128`

추가 판단:

- 이 plan은 구현 계획으로 바로 사용해도 된다.
- 다만 P0-2/P0-3과 함께 묶어야 한다. `ask_user` 표준화만 해도 file write/edit, skill execution, MCP mutation tool이 정책에 빠지면 안전성은 여전히 부족하다.
- frontend 작업은 단순히 `onStandardInterrupt`를 연결하는 수준이 아니라 standard interrupt payload를 assistant-ui synthetic tool call로 변환하는 coordinator가 필요하다. 현재 `UserInputUI`와 `ApprovalCard`는 단일 decision 즉시 resume 구조라 multi-action interrupt에 취약하다.

### 2.4 현재 plan과 memory 동작 상태

#### Plan / TodoList

현재 "plan"은 DeepAgents `TodoListMiddleware` 기준으로 부분적으로 동작한다.

동작하는 부분:

- Moldy는 모든 main agent를 `create_deep_agent()`로 만든다.
  - `backend/app/agent_runtime/executor.py:781-794`
- DeepAgents 0.6.1은 `TodoListMiddleware`를 main agent, custom subagent, 기본 `general-purpose` subagent stack에 자동 추가한다.
  - `deepagents/graph.py:547-548`
  - `deepagents/graph.py:619-620`
  - `deepagents/graph.py:670-673`
- `TodoListMiddleware`는 `write_todos` tool을 추가하고 graph state의 `todos`를 갱신한다.
  - installed `langchain/agents/middleware/todo.py`
- Moldy frontend에는 `write_todos` 전용 UI가 있다.
  - `frontend/src/components/chat/tool-ui/plan-tool-ui.tsx:52-55`
  - `frontend/src/lib/chat/tool-ui-registry.ts:31-47`
- main chat은 `thread_id = conversation_id`와 Postgres checkpointer를 사용하므로 같은 conversation 안에서는 todo state가 checkpoint에 보존될 수 있다.
  - `backend/app/agent_runtime/executor.py:789-797`

부족한 부분:

- plan은 제품 설정으로 켜고 끄는 기능이 아니다. DeepAgents built-in으로 항상 붙고, `todo_list` middleware 설정은 runtime에서 필터링된다.
  - `backend/app/agent_runtime/executor.py:667-673`
- UI는 `write_todos` tool call을 보여줄 뿐, 현재 todo state를 별도 API/side panel로 읽어오는 구조는 없다.
- SSE에는 `tool_call_id`가 없어 반복 plan update와 result 매칭이 취약하다.
- subagent 연결이 runtime에 전달되지 않으므로, 사용자가 만든 child agent의 plan이 parent/child 구조로 분리되어 동작하는 단계는 아니다.

결론:

- "DeepAgents plan 도구가 있나?"라는 의미라면 있다.
- "Moldy 제품 기능으로 계획 상태를 안정적으로 관리/조회/재개하나?"라는 의미라면 아직 아니다.

#### Memory

현재 "memory"는 DeepAgents `MemoryMiddleware` 기준으로 매우 얇게 동작한다.

동작하는 부분:

- `agent_id`가 있으면 `/agents/{agent_id}/AGENTS.md`가 memory source로 전달된다.
  - `backend/app/agent_runtime/executor.py:768-772`
- DeepAgents는 `memory` 인자가 있으면 `MemoryMiddleware`를 추가한다.
  - `deepagents/graph.py:718-727`
- `MemoryMiddleware`는 source file을 backend에서 읽고 system prompt에 `<agent_memory>` 블록으로 주입한다.
  - installed `deepagents/middleware/memory.py:290-349`
- Moldy는 agent memory directory를 만든다.
  - `backend/app/agent_runtime/executor.py:769-771`

부족한 부분:

- `AGENTS.md` 파일 자체를 생성하지 않는다. 파일이 없으면 DeepAgents는 오류 없이 skip하고 `(No memory loaded)` prompt가 들어간다.
- memory write는 `edit_file`을 통해 모델이 직접 파일을 수정하는 방식에 의존한다. 그런데 file permission/HITL 정책이 아직 정리되지 않았다.
- `StoreBackend`/`CompositeBackend`를 쓰지 않고 전역 `FilesystemBackend(root_dir=data)`를 쓴다.
- memory UI, audit, user approval, schedule mode memory write policy가 없다.
- MemoryMiddleware는 state에 `memory_contents`가 이미 있으면 다시 로드하지 않는다. 같은 checkpoint thread에서 memory file이 외부에서 바뀌어도 turn마다 최신 파일을 재로드한다는 보장이 약하다.

결론:

- "AGENTS.md를 읽어 prompt에 넣는 최소 연결"은 있다.
- "장기 메모리 제품 기능"은 아직 구현되지 않았다고 보는 편이 맞다.

### 2.5 `langfuse-trace-debugger-plan.md` 검증

이 plan은 현재 `message_events` 기반 SSE trace와 상호보완 관계가 명확하다. Moldy 내부 trace는 stream resume/share chip 렌더링에 좋고, Langfuse는 LangChain/LangGraph/DeepAgents 내부 span, LLM call, tool call, latency waterfall 디버깅에 더 적합하다.

유효한 주장:

- 현재 `message_events`는 assistant turn 단위 SSE event trace를 저장한다.
  - `backend/app/models/message_event.py:18-72`
  - `backend/app/services/trace_storage.py:60-181`
- stream run id는 이미 turn correlation key로 쓰인다.
  - 생성: `backend/app/routers/conversations.py:319-330`
  - partial flush: `backend/app/routers/conversations.py:334-357`
  - finalize: `backend/app/routers/conversations.py:360-398`
- Langfuse runtime integration dependency는 아직 없다. 다만 실제 `backend/.env`에는 사용자가 아래 키를 추가했고, repo 기준 `backend/.env.example`/`Settings`도 같은 이름으로 맞췄다.
  - `LANGFUSE_SECRET_KEY`
  - `LANGFUSE_PUBLIC_KEY`
  - `LANGFUSE_BASE_URL`
  - `backend/pyproject.toml`
  - `backend/.env.example`
  - `backend/app/config.py`
- Langfuse SDK v3 + LangChain callback 방향은 공식 문서와 맞다.
  - 공식 문서도 `from langfuse.langchain import CallbackHandler`와 `config={"callbacks": [handler]}` 형태를 안내한다.
  - SDK v3 self-hosted 요구 버전도 plan의 `>=3.125.0` 설명과 맞다.
- trace 단위를 "Langfuse trace = assistant turn 1회", "Langfuse session = conversation 1개"로 잡는 것은 현재 Moldy run_id/conversation_id 구조와 잘 맞다.

추가로 발견한 문제:

- 기존 `/api/conversations/{conversation_id}/traces` endpoint는 `get_current_user`와 ownership 검증이 없다.
  - `backend/app/routers/conversations.py:486-502`
  - 현재는 `chat_service.get_conversation()`만 호출한다.
  - trace event에는 tool args/results, user content, file/skill output이 들어갈 수 있으므로 Langfuse debugger 이전에 먼저 고쳐야 한다.
- Langfuse callback을 그대로 켜면 prompt, user input, tool args/result가 외부 trace backend로 나갈 수 있다. 현재 SSE event redaction과 별도의 external trace redaction/capture policy가 필요하다.
- `message_events` correlation 컬럼 추가는 좋은 방향이지만, `external_trace_id`를 SDK 생성 id로 받을지 deterministic `run_id`로 강제할지 먼저 검증해야 한다.
- Agent Prism은 alpha 성격이므로 core chat UI에 직접 강결합하지 않고 adapter/debug module로 격리해야 한다.

개선안:

- 단기 P0:
  - 기존 `/api/conversations/{conversation_id}/traces`에 `CurrentUser` dependency와 `get_owned_conversation()` guard 추가
  - share page용 trace 노출과 authenticated debug trace API를 명확히 분리
- P1:
  - `LANGFUSE_ENABLED`, key/base URL env와 `langfuse>=3.8,<4.0` dependency 추가
  - callback factory를 `executor.py`에 직접 흩뿌리지 말고 `observability/langfuse.py` 같은 작은 adapter로 격리
  - LangGraph config에 callback/metadata/tags 주입
  - `message_events`에 `external_trace_provider`, `external_trace_id`, `external_trace_url` 추가
  - backend proxy API에서 conversation ownership과 trace-session membership 검증
- P1/P2:
  - Agent Prism POC는 debug route/module로 격리
  - Langfuse 장애 시 `message_events` fallback UI 제공
  - input/output capture, redaction, sample rate를 env로 제어

## 3. 최종 우선순위별 상세 백로그

이 섹션이 실행 순서의 source of truth다. 뒤의 "영역별 상세 근거"는 각 항목의 소스 증거와 배경을 보존하기 위한 reference bank다.

### 1. existing trace endpoint access control

우선순위:

- P0

왜 먼저인가:

- 현재 `/api/conversations/{conversation_id}/traces`는 `get_current_user` 없이 `chat_service.get_conversation()`만 호출한다.
- `message_events`에는 user content, tool args/result, skill output, file content 일부가 들어갈 수 있다.
- Langfuse debugger를 붙이면 trace 표면적이 더 넓어지므로, 기존 trace endpoint 권한부터 닫아야 한다.

바로 할 일:

- `backend/app/routers/conversations.py`의 trace endpoint에 `CurrentUser = Depends(get_current_user)` 추가
- `chat_service.get_conversation()`을 `get_owned_conversation()` 또는 동일한 ownership guard로 교체
- public share page용 trace shape와 authenticated debug trace shape 분리
- cross-user/unauthenticated trace access regression test 추가

완료 기준:

- 다른 사용자의 `conversation_id`로 trace event를 조회할 수 없다.
- 인증 없는 요청은 trace를 받지 못한다.
- share page는 chip 렌더링에 필요한 최소 trace만 받는다.

### 2. middleware model credential boundary

우선순위:

- P0

왜 먼저인가:

- user-facing agent runtime에서 middleware model이 system/env credential로 생성될 수 있다.
- main model credential 정책은 비교적 엄격하지만, middleware model resolution이 우회 경로가 될 수 있다.
- 비용/보안/tenant isolation 문제가 동시에 걸려 있다.

바로 할 일:

- user conversation/trigger runtime에서 `provider_api_keys=env_provider_keys()` 전달 제거 또는 system flow 전용으로 분리
- `create_chat_model(..., allow_env_fallback=False)` 옵션 추가
- `_resolve_middleware_model_params()`에서 user-owned credential 없는 middleware model을 거절
- builder/assistant/system flow만 explicit env/system fallback 허용

완료 기준:

- user agent middleware model이 env/system credential로 생성되지 않는다.
- credential 누락은 조용한 fallback이 아니라 명확한 user-actionable error가 된다.

### 3. HITL/ask_user 표준 interrupt 연결

우선순위:

- P0

왜 먼저인가:

- 승인, 자연어 되묻기, subagent HITL 상속의 공통 wire다.
- 현재는 `ask_user`가 `interrupt_on` 계산 이후 추가되고, manual `HumanInTheLoopMiddleware` 주입으로 DeepAgents top-level 상속 경로를 잃는다.
- 이걸 먼저 정리해야 tool risk policy와 trigger guard가 같은 기준 위에 선다.

바로 할 일:

- `ask_user_tool`을 interrupt policy 계산 전에 추가
- manual `HumanInTheLoopMiddleware` instance append 제거
- `build_agent(..., interrupt_on=interrupt_on ...)`으로 DeepAgents top-level path 사용
- native `ask_user.py` fallback에서 standard resume payload의 `respond.message`만 추출
- `streaming.py` native adapter를 `review_configs[].action_name` 표준 shape로 고정
- frontend standard interrupt mapper/coordinator 추가

완료 기준:

- HITL 설정이 없어도 대화형 모드의 `ask_user`는 `respond` decision으로 resume된다.
- 기본 `general-purpose` subagent에도 top-level HITL policy가 상속된다.
- multi-action interrupt는 decision 배열 길이와 순서를 보존해 한 번에 resume된다.

### 4. tool risk policy와 trigger guard

우선순위:

- P0

왜 먼저인가:

- trigger/invoke mode는 사용자가 보고 있지 않아서 HITL을 끄지만, 현재 위험 도구의 대체 정책이 없다.
- Gmail/Calendar/webhook/skill execution 같은 외부 mutation이 예약 실행에서 무승인으로 나갈 수 있다.

바로 할 일:

- registry/builtin/MCP/skill tool에 `risk_level` 또는 `requires_approval` metadata 추가
- default HITL policy를 tool name heuristic이 아니라 risk metadata 기반으로 생성
- trigger/invoke mode에서 `external_mutation`, `code_execution` 기본 차단
- trigger run에 blocked reason 저장 및 UI 표시

완료 기준:

- 대화형 모드에서 mutation/code execution은 승인 없이 실행되지 않는다.
- trigger mode에서 위험 도구는 자동 실행되지 않는다.
- read-only tool은 불필요한 승인 없이 계속 실행된다.

### 5. filesystem permissions/CompositeBackend

우선순위:

- P0

왜 먼저인가:

- 현재 DeepAgents file tools가 같은 `backend/data` root를 본다.
- `virtual_mode=True`는 path escape 완화이지 user/agent/conversation ownership boundary가 아니다.
- memory, skill, conversation outputs 격리의 기반이다.

바로 할 일:

- `build_agent(..., permissions=...)` 추가
- agent/thread/user scoped permission builder 추가
- 최소 정책: current thread skill runtime read, current conversation output read/write, own agent memory policy-bound read/write, 나머지 `/skills/**`, `/agents/**`, `/runtime/**` deny
- 중기적으로 `CompositeBackend`로 temporary workspace, skills, outputs, memory route 분리

완료 기준:

- agent A가 agent B memory/conversation/skill을 읽거나 수정하지 못한다.
- selected skill만 read 가능하다.
- built-in `write_file`/`edit_file`이 permission과 HITL 정책을 모두 따른다.

### 6. execute_in_skill containment/sandbox

우선순위:

- P0

왜 먼저인가:

- 현재 `execute_in_skill`은 DeepAgents sandbox model을 우회해 host subprocess를 실행한다.
- `curl`과 credential env injection이 같이 있어 egress/secret exfiltration 리스크가 크다.

바로 할 일:

- 단기: `execute_in_skill`을 HITL 필수 또는 deny-by-default로 전환
- `curl` 허용 제거 또는 allowlist proxy tool로 대체
- stdout/stderr size limit, process group kill, concurrency limit 추가
- 중기: Docker/firecracker/isolated worker 등 sandbox로 이동
- 장기: sandbox backend 기반 DeepAgents built-in `execute`로 통합 검토

완료 기준:

- skill script는 selected skill root와 output mount 외부를 읽거나 쓰지 못한다.
- network egress는 policy에 따라 차단된다.
- credential env가 있더라도 stdout/stderr와 external egress로 새지 않는다.

### 7. MCP runtime credential/transport parity

우선순위:

- P0

왜 먼저인가:

- discovery에서 성공한 MCP가 runtime에서 raw headers/no interpolation/forced transport 때문에 실패할 수 있다.
- stdio는 discovery에는 있으나 runtime에서 빠질 수 있어 제품 신뢰도가 떨어진다.

바로 할 일:

- discovery/runtime 공통 connection builder 추가
- runtime에서 `build_headers()`/`build_env_vars()` 또는 공통 helper 사용
- `transport`, `url`, `command`, `args`, `env_vars`, `headers`, decrypted credentials를 runtime config에 전달
- stdio runtime 지원 또는 UI에서 runtime unsupported로 명확히 표시

완료 기준:

- discovery에서 성공한 credential-bound header/env가 runtime call에도 동일하게 적용된다.
- runtime transport가 discovery와 다르지 않다.
- unhealthy MCP server는 빠르게 설명 가능한 error로 실패한다.

### 8. sub-agent runtime 연결

우선순위:

- P0

왜 여덟 번째인가:

- 핵심 기능이지만 안전 경계 이전에 켜면 child agent가 file/tool/credential surface를 증폭할 수 있다.
- HITL top-level inheritance와 permission boundary가 먼저 있어야 안전하게 활성화할 수 있다.

바로 할 일:

- `AgentConfig.subagents` 추가
- child agent runtime assembly helper 추가
- `build_agent(..., subagents=...)` 추가
- child agent별 tools/skills/model/permissions/HITL inheritance 정책 구현
- depth 1부터 시작하고 multi-hop cycle 방지

완료 기준:

- parent에 연결한 child agent name이 `task` tool의 available subagent로 보인다.
- child prompt/tool/model이 실제로 사용된다.
- child agent도 top-level HITL/permission boundary를 벗어나지 못한다.

### 9. event stream/tool_call_id

우선순위:

- P1

왜 여기인가:

- tool result, plan update, subagent trace, Langfuse correlation이 안정적으로 맞물리려면 id 기반 event가 필요하다.
- 현재 frontend는 일반 tool result를 마지막 tool call에 붙인다.

바로 할 일:

- SSE `tool_call_start`/`tool_call_result`에 `tool_call_id` 추가
- backend에서 `tc.get("id")`와 ToolMessage `tool_call_id` 보존
- frontend result matching을 last call heuristic에서 id 기반으로 변경
- subagent event projection을 `agent_path`, `parent_tool_call_id`, `subagent_name`까지 확장 검토

완료 기준:

- 같은 tool을 연속 호출해도 result가 올바른 card에 붙는다.
- subagent tool calls가 parent tool calls와 구분된다.
- share trace chip/right rail/debug trace가 같은 id 체계를 쓴다.

### 10. streaming error observability

우선순위:

- P1

왜 여기인가:

- 현재 streaming path에서 error SSE를 emit하고도 hook/trace에서는 성공처럼 보일 수 있다.
- Langfuse debugger와 내부 trace 신뢰도를 위해 error status propagation이 먼저 필요하다.

바로 할 일:

- `stream_agent_response()`가 error 발생 여부를 typed result 또는 `error_sink`로 전달
- `_run_agent_stream()`이 hook failure/post success를 정확히 나누도록 변경
- `message_events.status`, trace sink, external trace metadata에 실패 상태 반영

완료 기준:

- 사용자에게 보인 streaming error가 backend observability에서도 failed로 기록된다.
- schedule/invoke/streaming path의 실패 의미가 일관된다.

### 11. Langfuse trace debugger POC

우선순위:

- P1

왜 열한 번째인가:

- 필요하지만 기존 trace endpoint access control, event id, streaming error status가 먼저 잡혀야 안전하고 정확한 debugger가 된다.
- `message_events`는 유지하고 Langfuse는 LangGraph/LLM/tool span waterfall을 보강하는 용도로 붙인다.

바로 할 일:

- `langfuse>=3.8,<4.0` dependency 추가
- `observability/langfuse.py` adapter 추가
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `LANGFUSE_ENABLED` settings 사용
- LangGraph config에 `CallbackHandler`, metadata, tags 주입
- `message_events`에 external trace correlation 컬럼 추가
- backend debug proxy API와 Agent Prism POC를 debug module로 격리

완료 기준:

- Langfuse trace가 assistant turn 단위로 생성된다.
- `conversation_id`가 Langfuse session으로 묶인다.
- Moldy run id와 Langfuse trace id가 1:1로 연결된다.
- Langfuse 장애 시 Moldy chat 실행은 실패하지 않고 `message_events` fallback이 표시된다.

## 4. 영역별 상세 근거

아래 항목은 원인과 소스 근거를 주제별로 모아 둔 reference bank다. 실행 순서는 위 3장, 8장, 10장의 우선순위를 따른다.

### P0-1. existing trace endpoint access control이 없다

현재 상태:

- `/api/conversations/{conversation_id}/traces` endpoint는 `get_current_user` dependency가 없다.
  - `backend/app/routers/conversations.py:486-502`
- ownership 검증도 `get_owned_conversation()`이 아니라 `chat_service.get_conversation()`만 호출한다.
  - `backend/app/routers/conversations.py:499-502`
- `MessageEvent.events`에는 SSE event sequence가 들어가며, tool args/result와 user/assistant content가 포함될 수 있다.
  - `backend/app/models/message_event.py:38-40`

왜 중요한가:

trace는 디버깅 데이터이지만 실제로는 대화 본문, tool input/output, skill 실행 결과가 섞이는 민감 데이터다. Langfuse debugger가 추가되면 debug surface가 더 커지므로, 기존 internal trace endpoint부터 auth/ownership 경계를 닫아야 한다.

개선안:

- endpoint에 `user: CurrentUser = Depends(get_current_user)` 추가
- `chat_service.get_owned_conversation(db, conversation_id, user.id)` 또는 동일한 ownership guard 사용
- public share page의 chip 렌더용 trace와 authenticated debug trace API를 분리
- cross-user/anonymous access regression test 추가

우선도:

- P0

### P0-8. UI/DB의 sub-agent가 실제 DeepAgents subagent로 동작하지 않는다

현재 상태:

- `Agent` 모델은 `sub_agent_links`를 가진다.
  - `backend/app/models/agent.py:80-86`
- 생성/수정 API는 `sub_agent_ids`를 저장한다.
  - `backend/app/services/agent_service.py:273-278`
  - `backend/app/services/agent_service.py:331-342`
- frontend도 payload에 `sub_agent_ids`를 넣는다.
  - `frontend/src/components/agent/visual-settings/visual-settings-flow.tsx:306-321`
- 응답 DTO에도 `sub_agents`가 표시된다.
  - `backend/app/routers/agents.py:80-88`
- 하지만 런타임 `AgentConfig`에는 subagent 필드가 없다.
  - `backend/app/agent_runtime/executor.py:157-199`
- `build_agent()`는 `create_deep_agent()`에 `subagents`를 전달하지 않는다.
  - `backend/app/agent_runtime/executor.py:360-387`
- `create_deep_agent()` 호출부도 `subagents` 없이 실행된다.
  - `backend/app/agent_runtime/executor.py:781-794`

왜 중요한가:

사용자가 UI에서 "이 agent는 저 agent에게 위임할 수 있다"고 설정하지만, 실제 DeepAgents `task` tool에는 기본 `general-purpose` subagent만 보인다. 제품의 핵심 기능이 저장만 되고 실행되지 않는 상태다.

개선안:

- `AgentConfig`에 `subagents: list[dict] | None` 추가
- `chat_service.get_owned_conversation_with_agent()`와 `get_agent_with_tools()`에서 child agent runtime 구성까지 로드
- child agent별로 다음을 DeepAgents `SubAgent` dict로 변환
  - `name`
  - `description`
  - `system_prompt`
  - `model`
  - `tools`
  - `skills`
  - `middleware`
  - `interrupt_on`
  - `permissions`
- 순환 참조 방지
  - parent == child 차단은 이미 있지만 multi-hop cycle은 별도 차단 필요
  - 우선 depth 1만 허용하는 것이 안전
- subagent name은 provider-safe canonical name으로 생성
  - 예: `agent_<slug>_<8chars>`
- 테스트 추가
  - `create_deep_agent` mock으로 `subagents` 전달 여부 확인
  - parent/child tool set이 분리되는지 확인
  - child prompt가 task 실행에 반영되는지 확인

우선도:

- P0

### P0-5. DeepAgents `permissions` 없이 `backend/data` 전체가 file tool에 노출된다

현재 상태:

- 모든 agent는 같은 data root를 backend로 사용한다.
  - `backend/app/agent_runtime/executor.py:719`
- `build_agent()` wrapper가 `permissions` 파라미터를 받지 않는다.
  - `backend/app/agent_runtime/executor.py:360-387`
- 설치된 DeepAgents 0.6.1의 `create_deep_agent()`는 `permissions`를 지원한다.
- DeepAgents 문서/소스 기준에서 permission rule이 없으면 file call은 허용된다.
- Memory path는 `/agents/{agent_id}/AGENTS.md`로 열린다.
  - `backend/app/agent_runtime/executor.py:768-772`
- Skill runtime은 per-thread로 mount되지만 canonical skill storage도 같은 data root 아래 있다.
  - canonical: `data/skills/<uuid>`
  - runtime: `data/runtime/<thread_id>/skills/<slug>`

왜 중요한가:

`virtual_mode=True`는 `../` escape를 막는 장치이지, app-level ownership 정책이 아니다. 현재 구조에서는 모델이 `ls("/")`, `read_file("/agents/...")`, `read_file("/skills/...")`, `write_file("/agents/...")` 같은 시도를 할 수 있다. UUID를 모르면 난이도는 올라가지만 보안 경계로 볼 수 없다.

LangChain/DeepAgents 기준:

- DeepAgents는 `permissions`로 built-in filesystem tools를 제어한다.
- persistent memory와 작업 파일은 `CompositeBackend`로 분리하는 패턴이 권장된다.
- Store 기반 장기 메모리를 쓰려면 `store`를 명시해야 한다.

개선안:

- `build_agent(..., permissions=...)` 파라미터 추가
- `_prepare_agent()`에서 agent/thread/user 기준 permission rule 생성
- 최소 기본 정책 예:
  - allow read: `/runtime/{thread_id}/skills/**`
  - allow read/write: `/conversations/{thread_id}/**`
  - allow read/write: `/agents/{agent_id}/AGENTS.md` only if memory write policy allows
  - deny read/write: `/skills/**`
  - deny read/write: `/agents/**`
  - deny read/write: `/runtime/**` except current thread
- `CompositeBackend` 재설계
  - default: `StateBackend` for temporary workspace
  - skills route: read-only filesystem copy
  - conversation outputs route: conversation-scoped filesystem
  - memory route: StoreBackend or DB-backed backend
- permission regression tests
  - agent A cannot read agent B memory
  - conversation A cannot read conversation B outputs
  - selected skill만 read 가능

우선도:

- P0

### P0-6. `execute_in_skill`이 DeepAgents sandbox 모델을 우회해 host에서 실행된다

현재 상태:

- DeepAgents 0.6.1의 built-in `execute`는 sandbox backend가 아니면 실행되지 않는다.
- 현재 `FilesystemBackend`는 `SandboxBackendProtocol`이 아니다.
- Moldy는 별도 `execute_in_skill` tool을 만들어 `asyncio.create_subprocess_exec()`로 host process를 실행한다.
  - `backend/app/agent_runtime/executor.py:236-357`
- 허용 executable:
  - `python`
  - `curl`
  - `backend/app/agent_runtime/executor.py:126-140`
- credential env injection도 이미 들어간다.
  - `backend/app/agent_runtime/executor.py:281-294`
- stdout/stderr redaction은 하지만 OS-level filesystem/network sandbox는 없다.
  - `backend/app/agent_runtime/executor.py:337-345`

왜 중요한가:

스크립트 path가 skill runtime root 하위인지 검사하는 것은 충분하지 않다. Python script는 서버 권한으로 실행되므로 절대경로 파일 읽기, 네트워크 요청, 장시간 CPU/메모리 사용, 내부 서비스 호출을 OS 차원에서 막지 못한다. `curl` 허용은 credential env가 들어간 상황에서 egress 리스크를 더 키운다.

개선안:

- 단기:
  - `execute_in_skill` 자동 실행을 기본 off 또는 HITL 필수로 전환
  - `curl` 허용 제거 또는 allowlist된 proxy tool로 대체
  - timeout 외에 stdout/stderr size limit, process group kill, concurrency limit 추가
  - 실행 전 `execution_profile.support_level`이 `ready_python`인 skill만 허용
- 중기:
  - sandbox backend 도입
  - Docker/firecracker/isolated worker 중 하나 선택
  - read-only skill mount + writable output mount만 제공
  - network default deny, egress allowlist 정책
- 장기:
  - DeepAgents built-in `execute`를 sandbox backend와 함께 사용하고 custom runner 제거

우선도:

- P0

### P0-3. HITL/ask_user 표준 interrupt wire가 잘못 연결되어 중요한 도구와 사용자 응답이 빠진다

현재 상태:

- auto `interrupt_on`은 `langchain_tools` 이름 중 write/send/delete/update/execute 등을 포함한 것만 대상으로 만든다.
  - `backend/app/agent_runtime/executor.py:675-696`
- 이 계산은 skill tool 추가보다 먼저 실행된다.
  - auto 계산: `backend/app/agent_runtime/executor.py:680-696`
  - `execute_in_skill` 추가: `backend/app/agent_runtime/executor.py:721-739`
- ask_user도 auto wrap 계산 이후 추가된다.
  - wrap 시도: `backend/app/agent_runtime/executor.py:703-706`
  - ask_user 추가: `backend/app/agent_runtime/executor.py:773-776`
- `ask_user`는 `interrupt_on`이 이미 있을 때만 표준 `respond` 정책에 추가된다.
  - `backend/app/agent_runtime/executor.py:703-706`
- DeepAgents built-in `write_file`, `edit_file`은 `langchain_tools`에 없으므로 auto 계산 대상이 아니다.
- `build_agent()`는 `interrupt_on=None`으로 DeepAgents 자동 HITL 주입을 끈다.
  - `backend/app/agent_runtime/executor.py:786-789`
- `HumanInTheLoopMiddleware`를 직접 넣는 방식이라 DeepAgents top-level `interrupt_on`의 subagent 상속 경로를 쓰지 못한다.
- native `ask_user` fallback은 resume payload에서 `respond.message`를 추출하지 않고 전체 dict를 문자열화한다.
  - `backend/app/agent_runtime/tools/ask_user.py:29-36`
- native ask_user interrupt adapter는 표준 `review_configs[].action_name` 대신 `tool_name`을 쓴다.
  - `backend/app/agent_runtime/streaming.py:111-116`
- 일반 대화 페이지는 `onStandardInterrupt`를 넘기지 않아 표준 interrupt payload가 실제 카드로 합성되지 않는다.
  - `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx:120-128`

왜 중요한가:

가장 위험한 도구인 file write/edit, skill execution, mutation tools가 기본 HITL에서 빠질 수 있다. 특히 P0-2처럼 file permissions가 없는 상태에서는 file write/edit이 더 중요하다. 또한 `ask_user`가 표준 `respond` decision으로 처리되지 않으면 사용자의 답변 대신 `{"decisions": [...]}` dict 문자열이 모델에 돌아갈 수 있고, 표준 승인/응답 카드가 UI에 나타나지 않을 수 있다.

개선안:

- 모든 tool assembly 이후에 `interrupt_on`을 계산한다.
- `ask_user_tool`을 대화형 모드에서 먼저 추가한 뒤, HITL middleware 설정 유무와 무관하게 `ask_user: {"allowed_decisions": ["respond"]}`를 merge한다.
- DeepAgents built-in tool names를 명시적으로 포함한다.
  - `write_file`
  - `edit_file`
  - `execute`
  - 필요하면 `task`
- `execute_in_skill`은 기본 approve/reject 대상이어야 한다.
- Gmail send, Calendar create/update/delete, Google Chat webhook 등 mutation registry tools에는 risk metadata를 추가하고 그 metadata로 HITL을 결정한다.
- `HumanInTheLoopMiddleware`를 직접 만들지 말고 `build_agent(... interrupt_on=interrupt_on ...)`으로 넘긴다. 이 경로가 DeepAgents 기본 `general-purpose` subagent와 declarative subagent 상속까지 처리한다.
- `ask_user.py` fallback은 `{"decisions": [{"type": "respond", "message": "..."}]}`에서 message만 추출한다.
- `streaming.py` native adapter는 `review_configs[].action_name = "ask_user"`만 사용한다.
- frontend는 `standardInterruptToToolCalls()` 같은 순수 mapping과 multi-action decision coordinator를 추가한다.
- explicit config가 없을 때의 default:

```python
interrupt_on = {
    "write_file": {"allowed_decisions": ["approve", "reject"]},
    "edit_file": {"allowed_decisions": ["approve", "edit", "reject"]},
    "execute": {"allowed_decisions": ["approve", "reject"]},
    "execute_in_skill": {"allowed_decisions": ["approve", "reject"]},
}
```

테스트:

- human_in_the_loop middleware만 추가해도 `write_file`, `edit_file`, `execute_in_skill`이 gate되는지 확인
- explicit `interrupt_on`이 있으면 명시 정책 우선
- HITL 설정이 없어도 대화형 모드에서는 `ask_user`가 표준 `respond` policy에 들어가는지 확인
- trigger mode에서는 `ask_user`와 `interrupt_on`이 모두 빠지는지 확인
- ask_user native fallback이 표준 resume payload에서 message만 반환하는지 확인
- standard interrupt payload의 multi-action decision 개수/순서가 보존되는지 확인

우선도:

- P0

### P0-4. trigger/schedule 실행은 HITL을 끄지만 대체 risk policy가 없다

현재 상태:

- trigger mode에서는 `interrupt_on = None`으로 강제된다.
  - `backend/app/agent_runtime/executor.py:698-701`
- ask_user tool도 trigger mode에서 제외된다.
  - `backend/app/agent_runtime/executor.py:773-776`
- 이는 hang 방지를 위해 필요하지만, mutation tool이 자동 실행되는 문제를 해결하지는 않는다.

왜 중요한가:

스케줄 실행은 사람이 보고 있는 채팅보다 더 엄격한 정책이 필요하다. Gmail 발송, Calendar 생성, 외부 webhook 호출, skill subprocess 같은 작업이 예약 실행에서 무승인으로 나갈 수 있다.

개선안:

- schedule/channel 실행용 tool risk policy 추가
  - `read_only`: 자동 허용
  - `write_internal`: pre-approved일 때 허용
  - `external_mutation`: 기본 차단 또는 approval inbox
  - `code_execution`: 기본 차단
- trigger 생성/수정 시 위험 도구가 있으면 UI 경고
- async approval inbox 추가
  - schedule run이 approval 필요 상태로 멈춤
  - owner에게 알림
  - 만료 시 auto reject
- trigger run status에 `waiting_approval` 추가

우선도:

- P0

### P0-2. user agent middleware model이 system/env credential을 사용할 수 있다

현재 상태:

- user conversation runtime은 `provider_api_keys=env_provider_keys()`를 넘긴다.
  - `backend/app/routers/conversations.py:128`
  - trigger도 동일: `backend/app/agent_runtime/trigger_executor.py:194`
- `_resolve_middleware_model_params()`는 middleware params의 `model`/`fallback_model` 문자열을 `create_chat_model()`로 미리 해석한다.
  - `backend/app/agent_runtime/executor.py:547-566`
- `create_chat_model()`은 `api_key`가 없으면 `_ENV_FALLBACK`을 사용한다.
  - `backend/app/agent_runtime/model_factory.py:139-140`
- `_ENV_FALLBACK`은 내부 caller용이라고 주석에 적혀 있지만, 현재 user agent middleware model resolution에도 들어간다.
  - `backend/app/agent_runtime/model_factory.py:51-59`

왜 중요한가:

ADR-016 이후 user-facing agent chat은 owner-registered credential로 실행되어야 한다. main model은 `resolve_llm_api_key_for_agent()`가 이를 강제하지만, middleware가 별도 model을 요구하는 경우 operator/system/env key를 사용할 여지가 있다.

개선안:

- user agent runtime에서는 `provider_api_keys`에 system/env fallback을 넣지 않는다.
- middleware model params는 다음 중 하나로 제한한다.
  - main model 재사용
  - user-owned credential이 명시된 model only
  - system flow(builder/assistant)에서만 system resolver 허용
- `create_chat_model(..., allow_env_fallback=False)` 옵션을 분리한다.
- `_resolve_middleware_model_params()`가 fallback 금지 모드에서 `api_key=None`이면 즉시 오류 처리한다.

우선도:

- P0

### P0-7. MCP discovery와 runtime credential/transport 처리가 다르다

현재 상태:

- discovery/probe 경로는 `resolve_deep()`으로 headers/env vars credential interpolation을 수행한다.
  - `backend/app/mcp/client.py:32-64`
  - `backend/app/mcp/discovery.py:31-43`
- runtime config는 server headers를 raw로 넘긴다.
  - `backend/app/services/chat_service.py:625-640`
- executor는 `mcp_transport_headers`를 그대로 사용하고 credential interpolation을 하지 않는다.
  - `backend/app/agent_runtime/executor.py:469-489`
- config에는 `"credentials": mcp_credentials`가 들어가지만 executor의 `_build_mcp_tools()`는 `auth_config`만 보고 있어 사실상 무시된다.
  - `backend/app/services/chat_service.py:637`
  - `backend/app/agent_runtime/executor.py:494-504`
- `stdio` MCP는 discovery에서는 지원되지만 runtime에서는 `server.url`이 없으면 건너뛴다.
  - `backend/app/services/chat_service.py:611-612`

왜 중요한가:

UI에서 "연결/발견 성공"한 MCP tool이 실제 agent runtime에서는 인증 실패하거나 아예 빠질 수 있다. 특히 remote MCP와 stdio MCP를 모두 지원한다고 보이는 제품에서는 신뢰도 문제가 크다.

개선안:

- discovery와 runtime이 동일한 connection builder를 사용하게 한다.
- runtime config에 다음을 모두 전달한다.
  - `transport`
  - `url`
  - `command`
  - `args`
  - `env_vars`
  - `headers`
  - decrypted credentials
- runtime `_build_mcp_tools()`에서 `build_headers()`/`build_env_vars()` 또는 공통 helper 사용
- `stdio` runtime 지원을 구현하거나, UI에서 "discovery only, runtime unsupported"로 명확히 표시
- MCP client/session caching 또는 lazy wrapper를 검토한다.

우선도:

- P0

## 5. P1: 기능은 되지만 신뢰도/성능/운영성이 부족한 항목

### P1-1. MCP tool loading이 매 turn 네트워크 discovery를 반복한다

현재 상태:

- `_prepare_agent()`는 매 실행마다 `_build_mcp_tools()`를 호출한다.
  - `backend/app/agent_runtime/executor.py:662-664`
- `_build_mcp_tools()`는 `MultiServerMCPClient(...).get_tools()`를 호출한다.
  - `backend/app/agent_runtime/executor.py:511-520`
- DB에는 이미 `mcp_tools.input_schema`와 `last_seen_at`이 저장되어 있다.
  - discovery path: `backend/app/mcp/discovery.py:82-110`

왜 중요한가:

MCP server가 느리거나 외부 네트워크에 있으면 첫 토큰 전 latency가 커진다. 연결 실패 시 전체 agent build가 늦어지고, 많은 MCP tool을 붙인 agent일수록 병목이 커진다.

개선안:

- runtime tool wrapper는 DB schema 기반으로 즉시 생성하고, 실제 call 시 client를 연다.
- server별 client/session pool을 둔다.
- health_status가 unhealthy인 server는 빠른 실패 stub로 대체한다.
- tool schema cache invalidation은 discovery/update 시점에 처리한다.

우선도:

- P1

### P1-2. skill runtime copytree가 매 turn event loop에서 동기 실행된다

현재 상태:

- `_prepare_agent()`에서 `build_skill_runtime_context()`를 직접 호출한다.
  - `backend/app/agent_runtime/executor.py:728`
- `build_skill_runtime_context()` 내부는 sync `mkdir`, `shutil.rmtree`, `shutil.copyfile`, `shutil.copytree`를 실행한다.
  - `backend/app/marketplace/skill_runtime.py:173-192`
  - `backend/app/marketplace/skill_runtime.py:248-261`
- helper docstring은 caller가 필요하면 `asyncio.to_thread`로 감싸라고 쓰여 있으나 현재 caller는 감싸지 않는다.
  - `backend/app/marketplace/skill_runtime.py:161-164`

왜 중요한가:

큰 `.skill` package나 여러 skill을 붙인 agent는 agent build 중 event loop를 블로킹한다. 동일 thread에서 매 turn target dir을 지우고 copy하므로 불필요한 IO도 크다.

개선안:

- `_prepare_agent()`에서 `await asyncio.to_thread(build_skill_runtime_context, ...)`
- content_hash 기반으로 이미 materialized된 skill은 skip
- target refresh는 atomic temp dir + rename
- max package size와 file count 제한을 runtime에도 적용
- cleanup job retention과 active run 상태를 함께 고려

우선도:

- P1

### P1-3. DeepAgents event stream 구조를 충분히 활용하지 못한다

현재 상태:

- `stream_agent_response()`는 `agent.astream(..., stream_mode="messages")`만 사용한다.
  - `backend/app/agent_runtime/streaming.py:273-278`
- tool call start는 message chunk의 `tool_calls`를 해석해 만든다.
  - `backend/app/agent_runtime/streaming.py:326-354`
- tool result는 `msg.type == "tool"`만 본다.
  - `backend/app/agent_runtime/streaming.py:356-368`
- SSE type에 `tool_call_id`가 없다.
  - `frontend/src/lib/types/index.ts:323-328`
- frontend는 일반 tool result를 마지막 tool call에 붙인다.
  - `frontend/src/lib/chat/use-chat-runtime.ts:431-442`

왜 중요한가:

같은 tool을 연속 호출하거나 subagent 내부 tool call이 섞이면 result가 잘못된 card에 붙을 수 있다. DeepAgents의 subagent lifecycle, nested path, parent/child relation도 UI에서 잃어버린다.

개선안:

- `astream_events` 또는 DeepAgents 공식 event projection 사용 검토
- SSE schema 확장
  - `tool_call_id`
  - `parent_tool_call_id`
  - `agent_path`
  - `subagent_name`
  - `run_id`
  - `status`
- backend에서 `tc.get("id")`와 ToolMessage `tool_call_id`를 보존해 emit
- frontend는 마지막 tool call이 아니라 `tool_call_id`로 result 매칭
- share trace chip과 right rail도 동일 id를 사용

우선도:

- P1

### P1-4. model fallback은 실제 LLM 호출 실패가 아니라 model construction 실패만 잡는다

현재 상태:

- executor의 `_build_model_with_fallback()`은 `create_chat_model()` 호출을 try/except한다.
  - `backend/app/agent_runtime/executor.py:570-617`
- `create_chat_model()`은 대부분 SDK wrapper 객체를 생성할 뿐 실제 API 호출은 streaming/invoke 시점에 발생한다.
  - `backend/app/agent_runtime/model_factory.py:121-161`
- 별도 `create_chat_model_with_fallback()`도 "we don't probe the model on every request"라고 명시한다.
  - `backend/app/agent_runtime/model_factory.py:435-445`
- fallback chain에는 model provider/name/base_url만 있고 credential resolving은 primary key 재사용 전제다.
  - `backend/app/routers/conversations.py:145-184`
  - `backend/app/agent_runtime/trigger_executor.py:30-62`

왜 중요한가:

429, 500, provider outage, auth error 같은 실제 fallback 대상은 대부분 LLM 호출 시점에 발생한다. 현재 구조에서는 fallback UI가 있어도 user-visible runtime failure를 충분히 회복하지 못할 가능성이 높다.

개선안:

- LangChain model fallback primitive 또는 middleware를 실제 model runnable에 적용한다.
- fallback model별 credential 정책을 명확히 한다.
  - same provider/same key만 허용
  - 또는 fallback model별 user-owned credential resolve
- streaming path에서 fallback 발생 사실을 trace/event에 남긴다.
- tests:
  - primary `astream`이 429를 던질 때 fallback model stream으로 이어지는지 확인
  - fallback provider가 다를 때 credential 누락 오류가 명확한지 확인

우선도:

- P1

### P1-5. memory는 파일 하나로 열려 있지만 제품 수준 장기 메모리 정책이 없다

현재 상태:

- agent id가 있으면 `/agents/{agent_id}/AGENTS.md`를 memory source로 넘긴다.
  - `backend/app/agent_runtime/executor.py:768-772`
- DeepAgents `MemoryMiddleware`는 memory file을 system prompt에 로드한다.
  - `deepagents/graph.py:718-727`
  - installed `deepagents/middleware/memory.py:290-349`
- 현재 코드는 agent memory directory만 만들고 `AGENTS.md` 파일은 만들지 않는다.
  - `backend/app/agent_runtime/executor.py:769-771`
- 파일 생성 lifecycle, write approval, memory UI, namespace policy는 없다.
- `store`는 `build_agent()`에 있지만 실제 user agent에서 전달하지 않는다.
  - `backend/app/agent_runtime/executor.py:360-387`

왜 중요한가:

장기 메모리는 사용자가 이해하고 통제해야 하는 제품 기능이다. 현재는 hidden file로만 동작하며, file write 권한과 결합하면 모델이 사용자 승인 없이 memory를 변경할 수 있다. 반대로 `AGENTS.md`가 없거나 모델이 memory 파일을 만들지 않으면 사용자는 memory가 켜져 있다고 기대하지만 실제로는 `(No memory loaded)`에 가까운 상태가 된다.

개선안:

- memory write policy 명시
  - off / chat-approved / schedule-disabled / auto
- agent 생성 시 empty `AGENTS.md`를 만들지, 첫 memory write 때 만들지 결정한다.
- AGENTS.md management UI 추가
- StoreBackend 또는 DB-backed Store 도입 검토
- per-user/per-agent namespace 설계
- schedule/channel 실행에서 memory write approval 정책 추가
- memory reload 정책 정의
  - 같은 conversation checkpoint에서 `memory_contents`가 이미 있으면 DeepAgents가 재로드를 skip하므로, 외부 UI에서 memory를 수정했을 때 새 run에 반영되는 조건을 명확히 해야 한다.

우선도:

- P1

### P1-6. streaming error가 hook failure로 기록되지 않는다

현재 상태:

- `stream_agent_response()` 내부에서 agent stream exception을 잡아 SSE `error`를 emit한다.
  - `backend/app/agent_runtime/streaming.py:383-389`
- 이 exception은 `_run_agent_stream()` 밖으로 전파되지 않는다.
  - `backend/app/agent_runtime/executor.py:917-942`
- 따라서 hook framework는 실패가 아니라 post success로 기록될 수 있다.

왜 중요한가:

사용자에게는 error가 보이지만 backend audit/usage/observability에서는 성공처럼 보일 수 있다. schedule/invoke path는 exception을 전파하지만 streaming path와 불일치한다.

개선안:

- `stream_agent_response()`가 error 발생 여부를 `error_sink`에 기록하거나 exception을 typed result로 반환
- `_run_agent_stream()`이 hook failure/post를 정확히 나눈다.
- trace_storage에도 turn status를 남긴다.

우선도:

- P1

### P1-7. assistant fixer agent가 DeepAgents built-in tools를 의도치 않게 가진다

현재 상태:

- assistant agent는 일반 runtime의 `build_agent()`를 사용한다.
  - `backend/app/agent_runtime/assistant/assistant_agent.py:84-91`
- `middleware=[]`를 넘겨도 DeepAgents built-in tool suite는 additive로 들어간다.
- DeepAgents 0.6.1 source 기준 built-ins:
  - `write_todos`
  - `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
  - `execute`는 sandbox backend에서만 실제 실행
  - `task`

왜 중요한가:

assistant fixer의 목적은 DB 설정을 읽고 제한된 write tools로 수정하는 것이다. 파일/태스크 도구가 섞이면 행동 범위가 넓어지고, UI/테스트가 기대하는 도구 표면과 실제 tool surface가 달라진다.

개선안:

- assistant는 LangChain `create_agent`로 분리하거나
- DeepAgents HarnessProfile의 `excluded_tools`로 built-in tools를 명시 제거하거나
- assistant용 `permissions`를 deny-all로 설정하고 `task` 사용 여부를 제품적으로 결정한다.

우선도:

- P1

## 6. P2: 유지보수/UX/문서 정합성 항목

### P2-1. middleware catalog와 assistant catalog가 서로 다르다

현재 상태:

- public `/api/middlewares`는 auto-injected middleware를 제외한다.
  - `backend/app/routers/agents.py:244-250`
  - `backend/app/agent_runtime/middleware_registry.py:464-480`
- assistant read tool은 `MIDDLEWARE_REGISTRY` 전체를 그대로 보여준다.
  - `backend/app/agent_runtime/assistant/tools/read_tools.py:143-154`
- assistant write tool은 registry에 있으면 저장하지만 executor는 `DEEPAGENT_BUILTIN_TYPES`를 다시 필터링한다.
  - `backend/app/agent_runtime/assistant/tools/write_tools.py:193-220`
  - `backend/app/agent_runtime/executor.py:667-673`

개선안:

- assistant도 `get_middleware_registry(exclude_builtin=True)` 사용
- auto-injected 항목은 "항상 포함됨"으로만 표시
- user-configurable middleware와 provider/internal middleware를 분리

우선도:

- P2

### P2-2. Anthropic prompt caching middleware가 중복될 수 있다

현재 상태:

- DeepAgents 0.6.1은 `AnthropicPromptCachingMiddleware`를 tail stack에 무조건 추가한다.
- Moldy도 provider가 anthropic이면 `get_provider_middleware()`에서 같은 middleware를 직접 추가한다.
  - `backend/app/agent_runtime/middleware_registry.py:414-427`
- 동시에 `anthropic_prompt_caching`은 auto-injected type으로 분류되어 있다.
  - `backend/app/agent_runtime/middleware_registry.py:430-440`

개선안:

- provider middleware에서 `anthropic_prompt_caching` 제거
- OpenAI moderation처럼 실제 DeepAgents가 자동 추가하지 않는 middleware만 provider auto로 유지
- runtime middleware stack snapshot test 추가

우선도:

- P2

### P2-3. `recursion_limit` 설정 도구가 실제 runtime에 반영되지 않는다

현재 상태:

- assistant write tool은 `model_params["recursion_limit"]`를 저장한다.
  - `backend/app/agent_runtime/assistant/tools/write_tools.py:631-649`
- read tool도 이 값을 보여준다.
  - `backend/app/agent_runtime/assistant/tools/read_tools.py:263-271`
- 하지만 `_prepare_agent()`의 LangGraph config에는 `thread_id`와 optional `checkpoint_id`만 들어간다.
  - `backend/app/agent_runtime/executor.py:796-803`
- DeepAgents는 compiled graph에 `recursion_limit=9999`를 기본 config로 붙인다.

개선안:

- `cfg.model_params.get("recursion_limit")`를 LangGraph config top-level에 넣는다.
- 또는 DeepAgents 기본을 쓰기로 결정하고 assistant 도구/문서를 제거한다.

우선도:

- P2

### P2-4. docs/PRD/marketplace spec가 현재 코드보다 오래되었다

현재 상태:

- `AGENTS.md`: 최신 migration을 M39라고 설명하지만 코드에는 M50까지 있다.
- `docs/PRD.md`: PoC/mock auth 서술이 남아 있다.
- `docs/marketplace-resources-prd.md`와 spec: broad `/skills/` mount가 현재 문제라고 쓰여 있으나 runtime은 per-thread mount로 바뀌었다.

개선안:

- `docs/ARCHITECTURE.md`, `PRD.md`, marketplace docs를 코드 기준으로 갱신
- "resolved since M47/M50" 같은 changelog 표기
- DeepAgents runtime 현황 표를 별도 유지

우선도:

- P2

## 7. 현재 잘 되어 있는 부분

아래는 유지할 가치가 있는 구조다.

- `create_deep_agent()` 호출이 `build_agent()`로 중앙화되어 있다.
  - `backend/app/agent_runtime/executor.py:360-387`
- conversation id를 LangGraph `thread_id`로 사용하는 방향은 맞다.
  - `backend/app/agent_runtime/executor.py:796-803`
- Postgres checkpointer singleton을 통해 HITL/resume 기반은 갖춰져 있다.
  - `backend/app/agent_runtime/checkpointer.py`
- DeepAgents `write_todos` plan tool은 자동 주입되고, frontend 전용 Plan card도 있다.
  - `frontend/src/components/chat/tool-ui/plan-tool-ui.tsx`
- skill broad mount 문제는 per-thread copytree와 prompt rewrite로 상당 부분 개선되었다.
  - `backend/app/marketplace/skill_runtime.py:232-268`
  - `backend/app/agent_runtime/executor.py:736-766`
- memory source를 `create_deep_agent(memory=...)`로 넘기는 최소 연결은 있다.
  - `backend/app/agent_runtime/executor.py:768-792`
- schedule run history는 첨부 문서 작성 이후 구현되어 있다.
  - `backend/app/models/agent_trigger_run.py`
  - `frontend/src/app/schedules/page.tsx:411-469`
- trigger ownership과 schema 정합성도 첨부 문서보다 개선되어 있다.
  - `backend/app/routers/triggers.py:119-143`
  - `backend/app/services/trigger_service.py:182-239`

## 8. 권장 구현 순서

### Milestone 0: Existing trace endpoint access control

목표: Langfuse debugger를 붙이기 전에 이미 존재하는 trace endpoint의 정보 노출 가능성을 닫는다.

1. `/api/conversations/{conversation_id}/traces`에 `CurrentUser = Depends(get_current_user)` 추가
2. `chat_service.get_conversation()` 대신 `get_owned_conversation()` 또는 동일한 ownership guard 사용
3. share page trace 노출과 authenticated debug trace API의 응답 shape를 분리
4. trace event redaction regression test 추가

완료 기준:

- 다른 사용자의 `conversation_id`로 trace event를 조회할 수 없다.
- unauthenticated request는 trace를 받지 못한다.
- public share page는 의도한 chip 렌더링용 최소 trace만 받는다.

### Milestone 1: Credential boundary quick fix

목표: 사용자 실행에서 운영자/system/env key가 섞이는 경로를 먼저 닫는다.

1. user-facing conversation/trigger runtime에서 `provider_api_keys=env_provider_keys()` 전달 제거 또는 system flow 전용으로 분리
2. `create_chat_model(..., allow_env_fallback=False)` 옵션 추가
3. `_resolve_middleware_model_params()`가 user-owned credential 없는 middleware model을 거절하도록 변경
4. builder/assistant 같은 system flow만 명시적으로 env/system fallback 허용

완료 기준:

- user agent의 middleware model이 env/system credential로 생성되지 않는다.
- main model과 middleware model credential 정책이 테스트로 분리된다.
- credential 누락 시 조용히 fallback하지 않고 user-actionable error가 나온다.

### Milestone 2: HITL and ask_user standardization

1. `ask_user_tool`을 interrupt policy 계산 전에 추가
2. manual `HumanInTheLoopMiddleware` 인스턴스 제거
3. `build_agent(..., interrupt_on=interrupt_on ...)`으로 DeepAgents top-level path 사용
4. native `ask_user.py` fallback resume parser 추가
5. `streaming.py` native adapter를 `action_name` 표준 shape로 고정
6. frontend standard interrupt mapper/coordinator 추가

완료 기준:

- HITL 설정이 없어도 대화형 모드의 `ask_user`는 `respond` decision으로 resume된다.
- 위험 도구 approval과 자연어 되묻기가 같은 표준 interrupt wire를 쓴다.
- 기본 `general-purpose` subagent에도 top-level HITL policy가 상속된다.
- multi-action interrupt는 decision 배열 길이와 순서를 보존해 한 번에 resume된다.

### Milestone 3: Tool risk policy and trigger guard

1. registry/builtin/MCP/skill tool에 `risk_level` 또는 `requires_approval` metadata 추가
2. default HITL policy를 tool name heuristic이 아니라 risk metadata 기반으로 생성
3. trigger/invoke mode에서는 `external_mutation`, `code_execution`을 기본 차단
4. trigger run에 blocked reason을 남기고 UI에서 원인을 표시

완료 기준:

- Gmail send, Calendar create/update/delete, webhook, `execute_in_skill`은 대화형 모드에서 승인 없이 실행되지 않는다.
- trigger mode에서 위험 도구는 자동 실행되지 않는다.
- 단순 read-only tool은 불필요한 승인 없이 계속 실행된다.

### Milestone 4: Filesystem permissions and skill containment

1. `build_agent(..., permissions=...)` 추가
2. agent/thread/user scoped permission builder 추가
3. built-in file tool 접근 regression tests
4. `execute_in_skill` HITL default gate
5. `curl` 제거 또는 allowlist proxy화

완료 기준:

- agent A가 agent B memory/conversation/skill을 읽지 못한다.
- selected skill만 읽을 수 있다.
- `write_file`/`edit_file`/`execute_in_skill`이 승인 없이 실행되지 않는다.

### Milestone 5: MCP runtime parity and external credential correctness

1. discovery/runtime 공통 connection builder
2. runtime credential interpolation 연결
3. stdio runtime 지원 여부 결정
4. MCP tool wrapper caching/lazy call

완료 기준:

- discovery에서 성공한 credential-bound header/env가 runtime call에서도 동일하게 적용된다.
- stdio server는 runtime 지원 또는 UI에서 명확히 차단된다.
- MCP 연결 실패가 첫 토큰 latency를 과도하게 늘리지 않는다.

### Milestone 6: Sub-agent runtime correctness

1. `AgentConfig.subagents` 추가
2. child agent runtime assembly helper 추가
3. `build_agent(..., subagents=...)` 추가
4. child agent별 tools/skills/model/permissions/HITL inheritance 정책 구현
5. subagent runtime tests 추가

완료 기준:

- parent에 연결한 child agent name이 `task` tool의 available subagent로 보인다.
- child prompt/tool/model이 실제로 사용된다.
- parent와 child tool 권한이 섞이지 않는다.
- child agent도 top-level HITL/permission boundary를 벗어나지 못한다.

### Milestone 7: Event streaming and trace fidelity

1. SSE에 `tool_call_id` 추가
2. frontend result matching을 id 기반으로 변경
3. DeepAgents subagent event projection 검토
4. trace_sink에 agent path/tool lifecycle 저장

완료 기준:

- 같은 tool을 연속 호출해도 결과가 정확한 card에 붙는다.
- subagent start/end/error가 parent run과 구분된다.
- share trace chip/right rail이 같은 id 체계를 쓴다.

### Milestone 8: Streaming error observability

1. `stream_agent_response`에 `error_sink` 또는 typed result 추가
2. `_run_agent_stream`에서 hook success/failure 기록 분리
3. `message_events.status`와 trace metadata에 failed/error 상태 반영
4. stream/invoke/trigger 실패 semantics 통일 테스트 추가

완료 기준:

- streaming error가 `message_events`와 trace/hook에서 성공이 아니라 실패로 남는다.
- scheduler/invoke/stream 실행의 실패 상태가 같은 방식으로 조회된다.
- Langfuse 연동 전에 내부 run failure metadata가 신뢰 가능한 상태가 된다.

### Milestone 9: Langfuse trace debugger POC

1. `langfuse>=3.8,<4.0` dependency와 `LANGFUSE_*` settings wiring 확정
2. `observability/langfuse.py` adapter 추가
3. LangGraph config에 `CallbackHandler`, metadata, tags 주입
4. `message_events` external trace correlation 컬럼 추가
5. backend debug proxy API 추가
6. conversation debug route 또는 drawer에 Agent Prism POC 연결

완료 기준:

- Langfuse trace가 assistant turn 단위로 생성된다.
- `conversation_id`가 Langfuse session으로 묶인다.
- Moldy run id와 Langfuse trace id가 1:1로 연결된다.
- 다른 사용자의 trace는 debug API로 조회할 수 없다.
- Langfuse 장애 시 Moldy chat 실행은 실패하지 않는다.

### Milestone 10: Runtime performance and reliability

1. MCP tool wrapper cache/lazy call
2. skill runtime copytree를 `asyncio.to_thread`와 content_hash cache로 변경
3. real model fallback을 construction-time이 아니라 invoke/stream-time fallback으로 구현

완료 기준:

- MCP가 느려도 첫 토큰 latency가 과도하게 늘지 않는다.
- 큰 skill package가 event loop를 블로킹하지 않는다.
- primary model runtime error에서 fallback 발생 여부가 trace에 남는다.

### Milestone 11: Memory and plan productization

1. memory write policy 정의
2. `AGENTS.md` 생성/부재/reload 정책 결정
3. StoreBackend 또는 DB-backed memory route 검토
4. memory management UI/audit 추가
5. Todo state 조회/side panel 또는 trace integration 설계

완료 기준:

- memory가 "숨은 파일"이 아니라 사용자가 이해하고 통제하는 기능이 된다.
- 다른 agent/conversation memory를 읽거나 수정할 수 없다.
- plan state를 tool card 외에도 안정적으로 조회할 수 있다.

### Milestone 12: Automatic run product surface

1. schedule/channel tool risk policy
2. async approval inbox model
3. trigger run `waiting_approval` status
4. channel delivery target 설계
5. agent identity mode 도입

완료 기준:

- 자동 실행에서 external mutation/code execution은 기본 차단 또는 approval pending이 된다.
- 사용자는 pending approval을 나중에 승인/거절할 수 있다.
- schedule/channel이 어떤 credential identity로 실행되는지 명확하다.

## 9. 검증 체크리스트

### Subagents

- parent A와 child B를 만든다.
- B에 A와 다른 system prompt와 tool set을 둔다.
- A에 B를 연결한다.
- A에게 B에게 위임하라고 요청한다.
- `task` call이 B의 canonical name으로 발생하는지 확인한다.
- B의 prompt/tool/model만 사용되는지 trace로 확인한다.

### Filesystem and permissions

- `read_file("/")`가 허용된 경로 외 목록을 보여주지 않는지 확인한다.
- `read_file("/skills/<uuid>/SKILL.md")`가 차단되는지 확인한다.
- `read_file("/runtime/<current_thread>/skills/<slug>/SKILL.md")`만 허용되는지 확인한다.
- `write_file("/agents/<other_agent>/AGENTS.md")`가 차단되는지 확인한다.
- `edit_file`도 같은 정책을 따르는지 확인한다.

### Skill execution

- unselected skill slug로 `execute_in_skill` 호출 시 거절된다.
- selected skill script가 timeout/size/concurrency 제한을 따른다.
- credential env가 stdout/stderr에 찍혀도 redaction된다.
- Python script가 host absolute path를 읽을 수 없는 sandbox로 격리된다.
- network egress가 policy에 맞게 차단된다.

### HITL

- human_in_the_loop middleware만 추가한 agent에서 `write_file`이 interrupt된다.
- `execute_in_skill`이 interrupt된다.
- Gmail send/Calendar create 등 mutation tool이 interrupt된다.
- HITL middleware 설정이 없어도 `ask_user`가 `respond` decision으로 interrupt된다.
- native `ask_user` fallback은 `{"decisions": [{"type": "respond", "message": "..."}]}`에서 message만 반환한다.
- 표준 interrupt payload는 `review_configs[].action_name`을 사용한다.
- 일반 대화 페이지에서 표준 interrupt payload가 `ask_user`/approval card로 렌더링된다.
- multi-action interrupt는 모든 decision을 모은 뒤 한 번만 resume한다.
- trigger mode에서 위험 도구가 자동 실행되지 않고 policy에 따라 차단/approval pending 된다.

### Plan / TodoList

- 긴 작업 요청 시 `write_todos` tool call이 발생한다.
- `write_todos` 결과가 Plan card로 렌더링된다.
- 같은 conversation의 다음 turn에서 graph state의 `todos`가 유지되는지 확인한다.
- 반복 plan update가 SSE `tool_call_id` 기반으로 올바른 card에 매칭된다.

### Memory

- 새 agent의 `/agents/{agent_id}/AGENTS.md` 생성/부재 정책이 명확하다.
- `AGENTS.md`에 저장한 내용이 다음 model call의 `<agent_memory>`에 들어간다.
- 다른 agent의 memory file을 읽거나 수정할 수 없다.
- 외부 UI에서 memory를 수정한 뒤 다음 run에 반영되는 reload 정책이 검증된다.
- schedule/channel 실행에서 memory write가 policy에 따라 차단 또는 승인 대기된다.

### MCP

- header interpolation credential이 runtime call에 적용된다.
- env var interpolation credential이 stdio runtime에 적용된다.
- 같은 MCP server의 같은 tool을 여러 번 호출해도 latency가 과도하지 않다.
- server unhealthy 상태에서 빠르게 설명 가능한 stub error가 나온다.

### Streaming

- tool_call_start와 tool_call_result에 같은 `tool_call_id`가 있다.
- 같은 tool이 연속 호출되어도 result가 올바르게 붙는다.
- subagent tool calls가 parent tool calls와 구분된다.
- streaming error가 hook failure/trace status에 남는다.

### Langfuse Debugger

- Langfuse disabled 상태에서 chat/resume/edit/regenerate가 기존처럼 동작한다.
- Langfuse enabled 상태에서 assistant turn마다 trace가 1개 생성된다.
- trace metadata에 user/conversation/agent/run/checkpoint/source가 들어간다.
- debug trace list/detail API가 conversation ownership을 검증한다.
- Langfuse API 장애 시 `message_events` 기반 fallback이 표시된다.
- capture input/output off, redaction on, sample rate 설정이 각각 동작한다.

## 10. 최종 우선순위 표

실행 순서 1-11이 이번 감사의 핵심 개선 순서다. 12번 이후는 correctness/security 경계가 선 뒤 진행할 후속 성능/제품화 항목이다.

| 실행 순서 | 우선순위 | 항목 | 이유 | 주요 파일 |
|---:|---|---|---|---|
| 1 | P0 | existing trace endpoint access control | 현재 `/api/conversations/{conversation_id}/traces`가 auth/ownership 없이 SSE trace를 반환할 수 있어 Langfuse 이전에 닫아야 한다. | `conversations.py`, `trace_storage.py` |
| 2 | P0 | middleware model credential boundary | 사용자 실행에서 system/env key가 섞이는 것은 인증/비용/격리 문제라 가장 먼저 닫아야 한다. | `executor.py`, `model_factory.py`, `conversations.py`, `trigger_executor.py` |
| 3 | P0 | HITL/ask_user 표준 interrupt 연결 | approval, ask_user, subagent inheritance의 공통 wire다. 이후 위험 도구 정책의 기반이다. | `executor.py`, `ask_user.py`, `streaming.py`, `use-chat-runtime.ts` |
| 4 | P0 | tool risk 기반 HITL policy와 trigger guard | 현재 trigger는 사람이 없어서 HITL을 끄지만 위험 도구 대체 정책이 없다. 자동 실행 사고를 먼저 막는다. | `executor.py`, tool registry, `trigger_service.py` |
| 5 | P0 | filesystem permissions/CompositeBackend | DeepAgents file tools가 전역 `data` root를 보는 상태라 memory/skill/conversation 격리의 기초가 필요하다. | `executor.py`, `skill_runtime.py` |
| 6 | P0 | `execute_in_skill` containment/sandbox | 단기 gate/curl 제거 후 sandbox/worker로 옮긴다. credential env와 host subprocess 조합이 가장 위험하다. | `executor.py`, marketplace skill runtime |
| 7 | P0 | MCP runtime credential/transport parity | discovery에서 성공한 MCP가 runtime에서 인증/transport mismatch로 실패하거나 raw secret interpolation이 누락된다. | `chat_service.py`, `executor.py`, `mcp/client.py` |
| 8 | P0 | sub-agent runtime 연결 | 핵심 기능이지만 안전 경계 후 켜야 child agent가 tool/permission surface를 증폭하지 않는다. | `executor.py`, `chat_service.py`, `agent_service.py` |
| 9 | P1 | DeepAgents event stream/tool_call_id | tool result, plan update, subagent trace가 안정적으로 맞물리려면 id 기반 event가 필요하다. | `streaming.py`, `use-chat-runtime.ts` |
| 10 | P1 | streaming error observability | 사용자에게는 error가 보이는데 backend는 성공처럼 기록되는 운영 리스크를 줄인다. | `streaming.py`, hooks/trace |
| 11 | P1 | Langfuse trace debugger POC | 내부 SSE trace를 유지하면서 LangGraph/LLM/tool span waterfall을 외부 observability로 보강한다. | `executor.py`, `message_event.py`, debug API/UI |
| 12 | P1 | MCP tool loading cache/lazy call | correctness 후 first-token latency와 MCP 장애 전파를 줄인다. | `executor.py`, MCP runtime |
| 13 | P1 | skill copytree async/cache | 큰 skill package가 event loop를 막는 성능 병목을 줄인다. | `skill_runtime.py`, `executor.py` |
| 14 | P1 | real model fallback | fallback UI와 실제 runtime 실패 회복을 일치시킨다. | `executor.py`, `model_factory.py` |
| 15 | P1 | memory product policy | 최소 연결은 있지만 사용자 통제/approval/store/reload 정책이 없다. FS 격리 이후에 제품화한다. | `executor.py`, memory UI |
| 16 | P1 | plan product state | `write_todos` tool은 있으나 제품 수준 조회/side panel/trace는 없다. event id 정리 후 다룬다. | chat UI, trace/right rail |
| 17 | P1 | assistant runtime separation | fixer agent가 DeepAgents built-ins를 의도치 않게 받는 문제를 닫는다. | `assistant_agent.py`, `executor.py` |
| 18 | P2 | middleware catalog 정리 | 사용자 설정 가능 항목과 auto/internal 항목을 분리한다. | `middleware_registry.py`, assistant read/write tools |
| 19 | P2 | provider middleware 중복 제거 | Anthropic prompt caching 중복 가능성을 줄인다. | `middleware_registry.py` |
| 20 | P2 | recursion_limit no-op 수정 | 설정값이 실제 runtime에 반영되지 않는 UX 정합성 문제다. | assistant tools, `executor.py` |
| 21 | P2 | 문서 갱신 | 코드와 PRD/AGENTS/marketplace docs 간 상태 차이를 줄인다. | `AGENTS.md`, `docs/PRD.md`, marketplace docs |

## 11. 한 줄 결론

현재 Moldy는 "DeepAgents 기반"이라는 방향은 맞지만, 아직 "DeepAgents harness를 Moldy의 권한/credential/subagent/skill/스케줄 제품 모델에 맞게 조립한 상태"는 아니다. 다시 정렬한 최우선 과제는 existing trace endpoint access control, credential boundary, HITL/ask_user 표준화, trigger risk guard, filesystem permission, skill execution containment다. Langfuse debugger는 필요하지만, 기존 trace API 권한과 event id/correlation을 먼저 정리한 뒤 붙이는 순서가 가장 덜 위험하다.
