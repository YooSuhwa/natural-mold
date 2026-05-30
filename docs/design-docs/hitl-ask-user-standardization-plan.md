# HITL Ask User Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DeepAgents 기반 메인 채팅에서 권한 승인 HiTL과 자연어 되묻기(`ask_user`)를 LangChain/DeepAgents 표준 interrupt 경로로 통일하고, 현재 wire/실행 순서 버그를 제거한다.

**Architecture:** 메인 채팅은 `create_deep_agent(interrupt_on=...)` 자동 주입 경로를 사용해 DeepAgents가 `HumanInTheLoopMiddleware`를 메인 에이전트와 subagent에 일관되게 적용하게 한다. `ask_user`는 LLM-visible tool로 유지하되 실제 대기는 `HumanInTheLoopMiddleware`의 `respond` decision으로 처리한다. Builder v3는 deterministic state machine이므로 기존 LangGraph native `interrupt()` 경로를 유지한다.

**Tech Stack:** FastAPI, LangChain 1.x, LangGraph 1.x, DeepAgents 0.6.x, React 19, assistant-ui, TanStack Query, Vitest, pytest.

---

## 1. 배경

ADR-012는 이미 `HumanInTheLoopMiddleware`와 `ask_user`의 책임을 분리했다.

- `HumanInTheLoopMiddleware`: 도구 실행 전 사용자 결정을 받는 표준 HiTL 미들웨어.
- 권한 승인: 위험 도구 실행 전에 `approve`, `edit`, `reject`를 받는 사용 사례.
- `ask_user`: 요청이 모호할 때 에이전트가 사용자에게 자연어 질문을 던지는 도구.
- `respond`: LangChain 표준 HiTL decision 중 하나로, 도구를 실제 실행하지 않고 사용자의 답변을 synthetic `ToolMessage`로 모델에 돌려준다.

따라서 "권한 승인"과 "되묻기"는 사용자 경험상 다르지만, 런타임 wire는 같은 표준 interrupt 체계를 사용할 수 있다. DeepAgents에서 `ask_user`를 처리하는 정석은 `ask_user`를 일반 tool로 노출하고, `interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}}`로 미들웨어가 tool 실행 전에 가로채게 하는 방식이다.

## 2. 현재 구현 상태

### 2.1 Backend

현재 메인 채팅은 다음 파일에 구현되어 있다.

- `backend/app/agent_runtime/executor.py`
- `backend/app/agent_runtime/tools/ask_user.py`
- `backend/app/agent_runtime/streaming.py`
- `backend/app/routers/conversations.py`
- `backend/app/schemas/conversation.py`

현재 구조의 핵심 흐름:

1. `executor.py`가 `middleware_configs`에서 `human_in_the_loop` 설정을 찾는다.
2. 쓰기/실행 도구 이름 또는 명시 `interrupt_on` dict로 정책을 만든다.
3. `HumanInTheLoopMiddleware(interrupt_on=...)`를 직접 생성해 `middleware` list에 넣는다.
4. `build_agent(... interrupt_on=None ...)`로 DeepAgents 자동 주입을 끈다.
5. `ask_user_tool`은 그 뒤에 `langchain_tools.append(ask_user_tool)`로 추가된다.

이 순서 때문에 `ask_user` 표준 wrap이 실제로 적용되지 않는다. `executor.py`의 `ask_user` 등록 체크는 `ask_user_tool` 추가 전에 수행되므로, 기본 경로에서는 `interrupt_on`에 `ask_user`가 들어가지 않는다.

### 2.2 Frontend

현재 프론트엔드는 표준 decision 타입을 이미 갖고 있다.

- `frontend/src/lib/chat/decision-mappers.ts`
- `frontend/src/components/chat/tool-ui/user-input-ui.tsx`
- `frontend/src/components/chat/tool-ui/approval-card.tsx`
- `frontend/src/lib/chat/use-chat-runtime.ts`

하지만 일반 대화 페이지와 builder 페이지는 `useChatRuntime`의 `onStandardInterrupt`를 전달하지 않는다.

- `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx`
- `frontend/src/app/agents/new/conversational/page.tsx`

`useChatRuntime`은 표준 interrupt payload를 받을 수 있지만, 실제 화면에 `ask_user` 또는 승인 카드를 합성해 넣는 책임이 아직 닫혀 있지 않다.

## 3. 문제점

### 3.1 `ask_user`가 표준 middleware 경로로 감싸지지 않는다

현재 `ask_user` tool은 `interrupt_on` 계산 후 추가된다. 따라서 `interrupt_on.setdefault("ask_user", {"allowed_decisions": ["respond"]})` 분기가 기본적으로 실행되지 않는다.

결과:

- 일반 채팅에서 `ask_user`는 LangChain middleware의 `respond` 경로가 아니라 tool body 내부의 native `interrupt()` 경로로 떨어질 수 있다.
- resume router는 항상 `Command(resume={"decisions": [...]})` 형태를 보낸다.
- native `interrupt()`는 raw resume 값을 받기 때문에 `ask_user.py`는 사용자의 답변이 아니라 `{"decisions": [{"type": "respond", "message": "..."}]}` 전체 dict를 `str(...)`로 모델에 반환할 수 있다.

### 3.2 DeepAgents subagent 상속을 잃는다

DeepAgents의 `create_deep_agent(interrupt_on=...)`는 내부적으로 메인 에이전트와 기본 `general-purpose` subagent에 `HumanInTheLoopMiddleware`를 주입한다.

현재 Moldy는 `HumanInTheLoopMiddleware`를 직접 만들어 `middleware` list에 넣고 `interrupt_on=None`을 넘긴다. 이 방식은 메인 에이전트에는 적용되지만, DeepAgents가 기본 subagent에 같은 정책을 자동 상속시키는 경로를 사용하지 못한다.

결과:

- 메인 에이전트가 직접 위험 도구를 호출하면 승인 게이트가 걸린다.
- 메인 에이전트가 `task`로 `general-purpose` subagent에 위임하고 subagent가 위험 도구를 호출하면 정책 누락 가능성이 생긴다.

### 3.3 표준 interrupt payload가 UI로 완전히 연결되지 않았다

`streaming.py`는 표준 `action_requests` / `review_configs` payload를 emit할 수 있다. 하지만 일반 대화 페이지는 `onStandardInterrupt`를 넘기지 않는다.

결과:

- 표준 middleware interrupt가 발생해도 UI가 승인/되묻기 카드를 확실하게 렌더링하지 못한다.
- 기존 `UserInputUI`와 `ApprovalCard`는 있지만, 표준 payload 배열을 assistant-ui tool card로 합성하는 coordinator가 부족하다.
- multi-action interrupt에서 카드별로 단일 decision을 즉시 resume하면 middleware가 기대하는 decision 개수와 맞지 않을 수 있다.

### 3.4 native `ask_user` adapter shape이 표준과 어긋날 수 있다

`streaming.py`의 native `ask_user` fallback adapter는 표준 `review_configs`의 키를 `action_name`으로 고정해야 한다. `tool_name` 같은 별도 키가 섞이면 frontend와 middleware mental model이 벌어진다.

## 4. 목표

- 메인 채팅의 `ask_user`는 항상 표준 `respond` decision으로 재개된다.
- 위험 도구 승인과 `ask_user` 되묻기는 하나의 `interrupt_on` 정책으로 합쳐진다.
- trigger/invoke 모드에서는 `ask_user` tool과 HiTL interrupt가 모두 비활성화된다.
- DeepAgents의 top-level `interrupt_on`을 사용해 기본 subagent에도 HiTL 정책이 상속된다.
- frontend는 표준 interrupt payload만으로 `ask_user` 카드와 승인 카드를 렌더링하고, multi-action decision을 한 번에 resume한다.
- Builder v3의 native `interrupt()` 흐름은 변경하지 않는다.

## 5. 비목표

- Builder v3 graph를 `HumanInTheLoopMiddleware`로 바꾸지 않는다.
- `ask_user` tool을 제거하지 않는다. 옵션 B는 이미 ADR-012에서 UX 손실 때문에 보류됐다.
- 전체 middleware registry를 재설계하지 않는다.
- DeepAgents permission sandbox나 `permissions` 옵션을 이번 작업 범위에 포함하지 않는다.

## 6. 권장 설계

### 6.1 Backend target flow

메인 채팅의 빌드 순서는 아래처럼 정리한다.

1. runtime tool, MCP tool, temporal tool, skill tool을 만든다.
2. 대화형 모드라면 `ask_user_tool`을 tool list에 추가한다.
3. `middleware_configs`에서 위험 도구 승인 정책을 만든다.
4. 대화형 모드라면 `ask_user` respond 정책을 항상 merge한다.
5. trigger 모드라면 `interrupt_on=None`으로 강제한다.
6. `HumanInTheLoopMiddleware`를 직접 만들지 않고 `build_agent(... interrupt_on=interrupt_on ...)`으로 넘긴다.

권장 helper:

```python
def _merge_interrupt_policy(
    base: dict[str, Any] | None,
    *,
    include_ask_user: bool,
) -> dict[str, Any] | None:
    policy = dict(base or {})
    if include_ask_user:
        policy["ask_user"] = {"allowed_decisions": ["respond"]}
    return policy or None
```

핵심은 `ask_user`가 위험 도구 승인 설정 유무와 무관하게 대화형 모드에서 표준 interrupt로 감싸져야 한다는 점이다.

### 6.2 `ask_user.py` 역할

정상 경로에서 `ask_user` tool body는 실행되지 않는다. middleware가 tool call 단계에서 interrupt를 발생시키고, `respond` decision을 synthetic `ToolMessage`로 만든다.

그래도 방어적으로 native fallback은 남긴다. fallback은 raw string과 표준 `{"decisions": [...]}` resume payload를 모두 처리해야 한다.

```python
def _extract_respond_message(response: object) -> str:
    if isinstance(response, dict):
        decisions = response.get("decisions")
        if isinstance(decisions, list) and decisions:
            first = decisions[0]
            if isinstance(first, dict) and first.get("type") == "respond":
                message = first.get("message")
                if isinstance(message, str):
                    return message
    return str(response)
```

### 6.3 Frontend target flow

표준 interrupt payload:

```ts
type StandardInterruptPayload = {
  interrupt_id?: string
  action_requests: Array<{
    name: string
    args: Record<string, unknown>
    description?: string
  }>
  review_configs: Array<{
    action_name: string
    allowed_decisions: Array<'approve' | 'edit' | 'reject' | 'respond'>
  }>
}
```

Frontend는 이 payload를 tool card로 합성한다.

- `ask_user` + `respond` only: `UserInputUI` 카드.
- 그 외 도구: `ApprovalCard` 카드. 실제 tool 이름은 `args.tool_name`에 넣는다.
- action이 여러 개면 decision coordinator가 모든 카드의 결정을 모은 뒤 `onResumeDecisions(decisions, displayText, interruptId)`를 한 번 호출한다.

이 책임은 각 페이지보다 `useChatRuntime` 또는 별도 `standard-interrupt-coordinator`에 두는 것이 낫다. 일반 대화 페이지가 `onStandardInterrupt`를 매번 직접 구현하면 builder/assistant 흐름과 쉽게 갈라진다.

## 7. 파일 구조

### Backend

- Modify: `backend/app/agent_runtime/executor.py`
  - tool 생성 순서 조정
  - `interrupt_on` 계산 helper 추가
  - manual `HumanInTheLoopMiddleware` append 제거
  - `build_agent(... interrupt_on=interrupt_on ...)` 사용

- Modify: `backend/app/agent_runtime/middleware_registry.py`
  - `human_in_the_loop`을 generic middleware builder에서 제외하는 정책은 유지
  - "executor가 직접 인스턴스화한다"는 주석을 "executor가 top-level `interrupt_on`으로 변환한다"로 갱신

- Modify: `backend/app/agent_runtime/tools/ask_user.py`
  - native fallback resume payload 파싱
  - docstring을 "정상 경로는 middleware respond" 중심으로 갱신

- Modify: `backend/app/agent_runtime/streaming.py`
  - native ask_user fallback adapter가 `review_configs[].action_name`을 사용하도록 고정
  - 표준 payload 검증을 더 엄격하게 유지

- Modify: `backend/tests/test_hitl_middleware.py`
  - 기존 "manual middleware instance" 가드를 "DeepAgents interrupt_on param" 가드로 교체
  - trigger mode `interrupt_on is None` 유지
  - `ask_user`가 explicit HITL 설정 없이도 포함되는지 검증
  - `human_in_the_loop` config가 generic middleware instance로 생성되지 않는지 검증

- Modify: `backend/tests/test_hitl_wire.py`
  - native ask_user adapter의 `action_name` 검증 추가
  - fallback resume payload가 `respond.message`만 반환하는 테스트 추가

### Frontend

- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
  - 표준 interrupt payload를 streaming message의 synthetic tool calls로 반영
  - multi-action decision coordinator 연결
  - `lastInterruptIdRef`를 resume 호출에 유지

- Create: `frontend/src/lib/chat/standard-interrupt.ts`
  - 표준 interrupt payload를 UI tool call args로 변환하는 순수 함수
  - action index와 `interrupt_id`를 안정적으로 포함

- Modify: `frontend/src/lib/chat/hitl-context.tsx`
  - 단일 decision 즉시 resume 외에 multi-action pending coordinator 지원
  - 기존 builder 흐름과 충돌하지 않도록 optional API로 확장

- Modify: `frontend/src/components/chat/tool-ui/user-input-ui.tsx`
  - coordinator가 있으면 action index에 decision을 기록
  - coordinator가 없으면 기존 단일 `[toRespond(message)]` resume 유지

- Modify: `frontend/src/components/chat/tool-ui/approval-card.tsx`
  - coordinator가 있으면 action index에 decision을 기록
  - coordinator가 없으면 기존 단일 decision resume 유지

- Modify: `frontend/src/lib/chat/tool-ui-registry.ts`
  - `ApprovalCard`의 synthetic tool name과 표준 interrupt mapping을 문서화

- Test: `frontend/src/lib/chat/__tests__/standard-interrupt.test.ts`
  - ask_user mapping
  - approval mapping
  - multi-action ordering

- Test: `frontend/src/lib/chat/__tests__/hitl-coordinator.test.tsx`
  - 모든 action 결정 전에는 resume하지 않음
  - 모든 action 결정 후 decision 배열을 순서대로 resume

## 8. 구현 작업

### Task 1: Backend regression tests를 먼저 갱신한다

**Files:**

- Modify: `backend/tests/test_hitl_middleware.py`
- Modify: `backend/tests/test_hitl_wire.py`

- [ ] **Step 1: manual middleware instance 기대를 제거한다**

현재 테스트는 `HumanInTheLoopMiddleware` 인스턴스가 `middleware` list에 들어가는 것을 기대한다. 목표 구조에서는 이 기대가 틀렸다. `build_agent`의 `interrupt_on` 인자가 정책을 받는지 검증하도록 바꾼다.

예상 assertion:

```python
build_kwargs = mock_build.call_args[1]
assert build_kwargs["interrupt_on"] == {"send_email": True}
assert not any(
    isinstance(m, HumanInTheLoopMiddleware)
    for m in (build_kwargs["middleware"] or [])
)
```

- [ ] **Step 2: explicit HITL 설정이 없어도 `ask_user`가 표준 정책에 들어가는 테스트를 추가한다**

예상 assertion:

```python
build_kwargs = mock_build.call_args[1]
assert build_kwargs["interrupt_on"] == {
    "ask_user": {"allowed_decisions": ["respond"]}
}
```

- [ ] **Step 3: trigger mode에서는 `ask_user`와 `interrupt_on`이 모두 빠지는 테스트를 추가한다**

예상 assertion:

```python
build_kwargs = mock_build.call_args[1]
tool_names = [t.name for t in mock_build.call_args.args[1]]
assert "ask_user" not in tool_names
assert build_kwargs["interrupt_on"] is None
```

- [ ] **Step 4: native ask_user adapter가 `action_name`을 쓰는지 검증한다**

예상 assertion:

```python
review = chunk["review_configs"][0]
assert review["action_name"] == "ask_user"
assert "tool_name" not in review
assert review["allowed_decisions"] == ["respond"]
```

- [ ] **Step 5: 테스트를 실행해 실패를 확인한다**

Run:

```bash
cd backend
uv run pytest tests/test_hitl_middleware.py tests/test_hitl_wire.py -q
```

Expected: 현재 구현에서는 `ask_user` 자동 정책, `interrupt_on` pass-through, `action_name` 검증 중 일부가 실패한다.

### Task 2: `executor.py`를 DeepAgents top-level `interrupt_on` 경로로 바꾼다

**Files:**

- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/agent_runtime/middleware_registry.py`

- [ ] **Step 1: `HumanInTheLoopMiddleware` 직접 import/use를 제거한다**

`executor.py`에서 아래 import를 제거한다.

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware
```

- [ ] **Step 2: interrupt policy helper를 추가한다**

권장 위치는 `_WRITE_TOOL_KEYWORDS` 아래다.

```python
def _infer_write_tool_interrupts(tools: list[BaseTool]) -> dict[str, Any] | None:
    policy = {
        t.name: True
        for t in tools
        if any(kw in t.name.lower() for kw in _WRITE_TOOL_KEYWORDS)
    }
    return policy or None


def _build_interrupt_on_policy(
    *,
    middleware_configs: list[dict[str, Any]] | None,
    tools: list[BaseTool],
    include_ask_user: bool,
    is_trigger_mode: bool,
) -> dict[str, Any] | None:
    if is_trigger_mode:
        return None

    interrupt_on: dict[str, Any] | None = None
    for mw_config in middleware_configs or []:
        if mw_config.get("type") != "human_in_the_loop":
            continue
        explicit = mw_config.get("params", {}).get("interrupt_on")
        if isinstance(explicit, dict) and explicit:
            interrupt_on = dict(explicit)
        else:
            interrupt_on = _infer_write_tool_interrupts(tools)
        break

    policy = dict(interrupt_on or {})
    if include_ask_user:
        policy["ask_user"] = {"allowed_decisions": ["respond"]}
    return policy or None
```

- [ ] **Step 3: `ask_user_tool`을 policy 계산 전에 추가한다**

현재 `ask_user_tool` 추가 블록을 `interrupt_on` 계산보다 앞으로 이동한다. skill `execute_in_skill`처럼 뒤늦게 추가되는 tool이 있으면 policy 계산 시점도 그 뒤로 옮긴다.

원칙:

```python
if not is_trigger_mode:
    langchain_tools.append(ask_user_tool)

interrupt_on = _build_interrupt_on_policy(
    middleware_configs=cfg.middleware_configs,
    tools=langchain_tools,
    include_ask_user=not is_trigger_mode,
    is_trigger_mode=is_trigger_mode,
)
```

- [ ] **Step 4: manual middleware append를 제거한다**

아래 형태의 코드를 삭제한다.

```python
if interrupt_on:
    middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
```

- [ ] **Step 5: `build_agent`에 `interrupt_on`을 넘긴다**

```python
agent = build_agent(
    model,
    langchain_tools,
    system_prompt,
    middleware=middleware or None,
    interrupt_on=interrupt_on,
    checkpointer=get_checkpointer(),
    backend=backend,
    skills=skills_sources,
    memory=memory_sources,
    name=f"agent_{cfg.thread_id[:8]}",
)
```

- [ ] **Step 6: `middleware_registry.py` 주석을 새 책임에 맞춘다**

`EXPLICITLY_INSTANTIATED_TYPES` 이름을 유지할지 바꿀지는 구현자가 선택할 수 있다. 최소 변경으로 가려면 set은 그대로 두고 주석만 아래 의미로 바꾼다.

```python
# 본 set 의 항목은 ``build_middleware_instances`` 경로를 우회한다. executor 가
# config 를 읽어 DeepAgents top-level ``interrupt_on`` 인자로 변환한다.
# 직접 ``HumanInTheLoopMiddleware`` 인스턴스를 만들지 않는 이유는
# ``create_deep_agent(interrupt_on=...)`` 경로가 기본 subagent 상속까지 처리하기 때문이다.
```

- [ ] **Step 7: backend tests를 실행한다**

Run:

```bash
cd backend
uv run pytest tests/test_hitl_middleware.py tests/test_hitl_wire.py -q
```

Expected: Task 1의 backend regression tests가 통과한다.

### Task 3: `ask_user.py` fallback을 표준 resume payload와 호환되게 한다

**Files:**

- Modify: `backend/app/agent_runtime/tools/ask_user.py`
- Modify: `backend/tests/test_hitl_wire.py`

- [ ] **Step 1: fallback parser 단위 테스트를 추가한다**

예상 테스트:

```python
from app.agent_runtime.tools.ask_user import _extract_respond_message


def test_extract_respond_message_from_standard_resume_payload():
    assert _extract_respond_message(
        {"decisions": [{"type": "respond", "message": "옵션 A"}]}
    ) == "옵션 A"


def test_extract_respond_message_falls_back_to_string():
    assert _extract_respond_message("옵션 B") == "옵션 B"
```

- [ ] **Step 2: parser를 구현하고 `ask_user` 반환값에 적용한다**

```python
response = interrupt(
    {
        "type": "ask_user",
        "question": question,
        "options": options or [],
    }
)
return _extract_respond_message(response)
```

- [ ] **Step 3: 테스트를 실행한다**

Run:

```bash
cd backend
uv run pytest tests/test_hitl_wire.py -q
```

Expected: fallback parser 테스트와 기존 wire 테스트가 통과한다.

### Task 4: `streaming.py` native adapter를 표준 shape로 고정한다

**Files:**

- Modify: `backend/app/agent_runtime/streaming.py`
- Modify: `backend/tests/test_hitl_wire.py`

- [ ] **Step 1: `_interrupt_to_standard_chunk`의 ask_user fallback을 점검한다**

native fallback 결과는 아래 형태여야 한다.

```python
{
    "interrupt_id": intr_id,
    "action_requests": [
        {
            "name": "ask_user",
            "args": {"question": question, "options": options},
        }
    ],
    "review_configs": [
        {
            "action_name": "ask_user",
            "allowed_decisions": ["respond"],
        }
    ],
}
```

- [ ] **Step 2: `tool_name` 또는 비표준 키가 있으면 제거한다**

표준 payload에서는 `review_configs[].action_name`을 사용한다.

- [ ] **Step 3: 테스트를 실행한다**

Run:

```bash
cd backend
uv run pytest tests/test_hitl_wire.py -q
```

Expected: 표준 chunk tests가 통과한다.

### Task 5: Frontend standard interrupt mapping 순수 함수를 만든다

**Files:**

- Create: `frontend/src/lib/chat/standard-interrupt.ts`
- Create: `frontend/src/lib/chat/__tests__/standard-interrupt.test.ts`

- [ ] **Step 1: ask_user mapping 테스트를 작성한다**

```ts
import { describe, expect, it } from 'vitest'
import { standardInterruptToToolCalls } from '../standard-interrupt'

describe('standardInterruptToToolCalls', () => {
  it('maps ask_user respond action to ask_user tool UI args', () => {
    const calls = standardInterruptToToolCalls({
      interrupt_id: 'intr-1',
      action_requests: [
        { name: 'ask_user', args: { question: '어느 쪽?', options: ['A', 'B'] } },
      ],
      review_configs: [
        { action_name: 'ask_user', allowed_decisions: ['respond'] },
      ],
    })

    expect(calls).toHaveLength(1)
    expect(calls[0].name).toBe('ask_user')
    expect(calls[0].args).toMatchObject({
      question: '어느 쪽?',
      options: ['A', 'B'],
      approval_id: 'intr-1:0',
      hitl_action_index: 0,
      hitl_total_actions: 1,
    })
  })
})
```

- [ ] **Step 2: approval mapping 테스트를 작성한다**

```ts
it('maps non-ask_user action to request_approval synthetic tool UI args', () => {
  const calls = standardInterruptToToolCalls({
    interrupt_id: 'intr-2',
    action_requests: [
      { name: 'send_email', args: { to: 'a@example.com' }, description: 'Send email' },
    ],
    review_configs: [
      { action_name: 'send_email', allowed_decisions: ['approve', 'edit', 'reject'] },
    ],
  })

  expect(calls[0].name).toBe('request_approval')
  expect(calls[0].args).toMatchObject({
    tool_name: 'send_email',
    tool_args: { to: 'a@example.com' },
    description: 'Send email',
    allowed_decisions: ['approve', 'edit', 'reject'],
    approval_id: 'intr-2:0',
  })
})
```

- [ ] **Step 3: mapping 함수를 구현한다**

```ts
import type { StandardInterruptPayload, ToolCallInfo } from '@/lib/types'

export function standardInterruptToToolCalls(payload: StandardInterruptPayload): ToolCallInfo[] {
  return payload.action_requests.map((request, index) => {
    const review = payload.review_configs[index]
    const baseArgs = {
      ...request.args,
      approval_id: `${payload.interrupt_id ?? 'interrupt'}:${index}`,
      hitl_interrupt_id: payload.interrupt_id ?? null,
      hitl_action_index: index,
      hitl_total_actions: payload.action_requests.length,
      allowed_decisions: review?.allowed_decisions ?? [],
    }

    const respondOnly =
      request.name === 'ask_user' &&
      review?.allowed_decisions.length === 1 &&
      review.allowed_decisions[0] === 'respond'

    if (respondOnly) {
      return {
        id: `hitl-${payload.interrupt_id ?? 'interrupt'}-${index}`,
        name: 'ask_user',
        args: baseArgs,
      }
    }

    return {
      id: `hitl-${payload.interrupt_id ?? 'interrupt'}-${index}`,
      name: 'request_approval',
      args: {
        ...baseArgs,
        tool_name: request.name,
        tool_args: request.args,
        description: request.description,
      },
    }
  })
}
```

- [ ] **Step 4: frontend 테스트를 실행한다**

Run:

```bash
cd frontend
pnpm test -- --run src/lib/chat/__tests__/standard-interrupt.test.ts
```

Expected: mapping tests가 통과한다.

### Task 6: Frontend multi-action coordinator를 연결한다

**Files:**

- Modify: `frontend/src/lib/chat/hitl-context.tsx`
- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
- Modify: `frontend/src/components/chat/tool-ui/user-input-ui.tsx`
- Modify: `frontend/src/components/chat/tool-ui/approval-card.tsx`
- Create: `frontend/src/lib/chat/__tests__/hitl-coordinator.test.tsx`

- [ ] **Step 1: context에 action decision 등록 API를 추가한다**

기존 `onResumeDecisions`는 유지한다. 새 API는 optional로 둔다.

```ts
type RegisterHiTLDecision = (
  actionIndex: number,
  decision: Decision,
  displayText?: string,
) => Promise<void>
```

- [ ] **Step 2: `useChatRuntime`에서 interrupt 도달 시 pending coordinator를 초기화한다**

`case 'interrupt'`에서 `standardInterruptToToolCalls(data)`를 호출하고, synthetic tool calls를 현재 streaming assistant message에 추가한다.

중요 조건:

- `data.action_requests.length === 1`이면 기존 단일 resume 흐름과 동일하게 동작해야 한다.
- `data.action_requests.length > 1`이면 모든 action index의 decision이 들어올 때까지 resume하지 않는다.
- resume 시 decision 배열 순서는 `action_requests` 순서와 같아야 한다.

- [ ] **Step 3: `UserInputUI`와 `ApprovalCard`가 coordinator를 우선 사용하게 한다**

카드 args에 `hitl_action_index`가 있으면:

```ts
await hitl?.registerDecision?.(hitlActionIndex, toRespond(message), displayText)
```

없으면 기존:

```ts
await hitl?.onResumeDecisions([toRespond(message)], displayText)
```

를 유지한다.

- [ ] **Step 4: coordinator 테스트를 작성한다**

검증:

- action 2개 중 1개만 결정하면 `streamResumeDecisions`가 호출되지 않는다.
- 2개 모두 결정하면 `streamResumeDecisions`가 한 번 호출된다.
- decision 배열 순서는 action index 기준이다.
- `interrupt_id`가 resume payload에 포함된다.

- [ ] **Step 5: frontend 테스트를 실행한다**

Run:

```bash
cd frontend
pnpm test -- --run src/lib/chat/__tests__/standard-interrupt.test.ts src/lib/chat/__tests__/hitl-coordinator.test.tsx
```

Expected: standard interrupt mapping과 coordinator tests가 통과한다.

### Task 7: 페이지 연결과 회귀 확인

**Files:**

- Modify: `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx`
- Modify: `frontend/src/app/agents/new/conversational/page.tsx`

- [ ] **Step 1: 일반 채팅 페이지는 추가 `onStandardInterrupt` 구현 없이 동작하는지 확인한다**

Task 6에서 `useChatRuntime` 내부가 표준 interrupt를 처리한다면 페이지 변경은 최소화한다.

- [ ] **Step 2: Builder v3 페이지에서 기존 native 흐름이 깨지지 않는지 확인한다**

Builder는 이미 자체 tool UI와 resume adapter를 사용한다. 이번 작업에서 Builder graph를 수정하지 않는다.

- [ ] **Step 3: 전체 frontend 검증을 실행한다**

Run:

```bash
cd frontend
pnpm lint
pnpm test -- --run
pnpm build
```

Expected: lint, vitest, Next build가 통과한다.

### Task 8: 통합 검증

**Files:**

- No direct code changes.

- [ ] **Step 1: backend 전체 HiTL 관련 테스트를 실행한다**

Run:

```bash
cd backend
uv run pytest tests/test_hitl_middleware.py tests/test_hitl_wire.py tests/test_builder_resume_wire.py -q
```

Expected: 모든 테스트가 통과한다.

- [ ] **Step 2: backend lint를 실행한다**

Run:

```bash
cd backend
uv run ruff check app tests
```

Expected: ruff 위반이 없다.

- [ ] **Step 3: 수동 시나리오를 확인한다**

확인 시나리오:

- 일반 대화에서 모호한 요청을 보내 `ask_user` 카드가 뜬다.
- 사용자가 답하면 backend resume payload가 `{"decisions": [{"type": "respond", "message": "..."}]}` 형태로 전송된다.
- 모델이 사용자의 답변 내용을 그대로 이해하고 다음 응답을 이어간다.
- 위험 도구가 설정된 agent에서 승인 카드가 뜬다.
- `approve`, `reject`, `edit` decision이 각각 동작한다.
- trigger 실행에서는 `ask_user`가 tool list에 없고 interrupt가 발생하지 않는다.
- subagent가 위험 도구를 호출하는 시나리오에서 같은 approval 정책이 적용된다.

## 9. 권장 커밋 순서

1. `test(hitl): pin deepagents interrupt_on policy`
2. `fix(runtime): route chat hitl through deepagents interrupt_on`
3. `fix(runtime): normalize ask_user fallback resume payload`
4. `feat(chat): map standard interrupts to hitl tool cards`
5. `feat(chat): coordinate multi-action hitl decisions`
6. `test(chat): cover ask_user and approval interrupt flows`

## 10. 완료 기준

- `build_agent`가 일반 채팅에서 `interrupt_on`을 받는다.
- 일반 채팅에서 `HumanInTheLoopMiddleware` 직접 인스턴스가 `middleware` list에 추가되지 않는다.
- `ask_user`는 explicit HITL middleware 설정이 없어도 `{"allowed_decisions": ["respond"]}` 정책에 포함된다.
- trigger mode에서는 `ask_user`와 `interrupt_on`이 모두 비활성화된다.
- 표준 interrupt payload만으로 `UserInputUI`와 `ApprovalCard`가 렌더링된다.
- multi-action interrupt는 모든 decision을 모아 한 번만 resume한다.
- native `ask_user` fallback은 `{"decisions": [...]}` dict를 모델에 그대로 노출하지 않는다.
- Builder v3 회귀가 없다.

## 11. 남은 결정 사항

1. 위험 도구 승인 정책을 "HITL middleware가 설정된 agent에만" 적용할지, 아니면 특정 write keyword 도구에 product default로 항상 적용할지 결정해야 한다. 현재 권장안은 보수적으로 기존 behavior를 유지해 `human_in_the_loop` config가 있을 때만 위험 도구 자동 추출을 적용하고, `ask_user`만 일반 채팅에서 항상 표준 wrap한다.
2. multi-action UI에서 각 카드를 개별 제출 버튼으로 둘지, 카드별 선택 후 하단의 "모두 제출" 버튼을 둘지 결정해야 한다. 구현 안정성은 coordinator + 자동 일괄 제출이 가장 낮은 변경량이다.
3. subagent approval 상속은 unit test만으로 완전히 보장하기 어렵다. 가능하면 DeepAgents integration test 또는 mocked source-level regression guard를 추가한다.
