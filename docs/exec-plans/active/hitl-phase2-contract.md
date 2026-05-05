# HiTL Phase 2 — Wire Contract (Backend ↔ Frontend Single Source of Truth)

> **DRI**: 피차이 (M0). 이 문서는 젠슨(M1, backend) + 저커버그(M2, frontend)가 같은 PR 안에서 양면 동시 구현하기 위한 단일 진실 공급원이다. wire 디테일은 본 문서 한 곳에서만 결정된다 — ADR-012 §3, §4, §7의 추상 계약을 라인-레벨로 구체화한다.
>
> **상위 계약**: `docs/design-docs/adr-012-hitl-middleware-migration.md` (특히 §3, §4, §7, Phase 2)
> **상태**: APPROVED — 후속 마일스톤(M1·M2) 진입 게이트
> **브랜치**: `feature/hitl-phase2-wire-format` (main `750d587`에서 분기)
> **Phase 1 산출물 보존**: `backend/app/agent_runtime/middleware_registry.py`, `backend/app/agent_runtime/executor.py:_prepare_agent` 시그니처, `backend/tests/test_hitl_middleware.py` 5건 — Phase 2에서 일체 미수정.

---

## 1. 목적 — Transition Window 정책

Phase 2는 **단일 PR로 표준 wire와 legacy wire 양쪽이 동시에 작동**하는 dual-path transition window이다. Phase 3에서 legacy 경로(자체 `{interrupt_id, value}` chunk + `response` 필드)가 제거된다. 본 PR의 done-when:

- 표준 클라이언트(M2 갱신본) ↔ 표준 백엔드(M1 갱신본) → 표준 wire로 정상 동작
- 표준 클라이언트 ↔ legacy 백엔드(이론상; 실제 머지 후엔 동시 갱신) → legacy wire 어댑터로 정상 동작
- legacy 클라이언트(전면 배포 직전 캐시 등) ↔ 표준 백엔드 → legacy chunk로 정상 동작
- 회귀 0 — 기존 826 backend test + 기존 frontend test 전부 PASS

**규칙**: dual-path의 **양쪽이 한 PR에 같이 들어간다**. 한쪽만 머지되는 시나리오는 정의하지 않는다 (테스트만 양쪽을 검증한다).

---

## 2. Pydantic 스키마 — `Decision` + `ResumeRequest`

**파일**: `backend/app/schemas/conversation.py`

### 2.1 `Decision`

LangChain 표준 `HITLResponse.decisions[i]` 구조와 1:1 매칭. 자체 Pydantic 모델로 정의해 router 레이어에서 검증한 뒤 dict로 직렬화하여 `Command(resume={"decisions": [dict, ...]})` 로 넘긴다 (LangChain 미들웨어는 TypedDict를 받음).

```python
from typing import Any, Literal
from pydantic import BaseModel, model_validator

class Decision(BaseModel):
    """단일 tool_call에 대한 인간 결정.

    LangChain `HumanInTheLoopMiddleware`의 `HITLResponse.decisions[i]` 와 동일 shape:
    - approve: 추가 필드 없음
    - edit: edited_action={"name": str, "args": dict} 필수
    - reject: message는 선택 (없으면 미들웨어가 기본 메시지 생성)
    - respond: message 필수 (synthetic ToolMessage content)
    """
    type: Literal["approve", "edit", "reject", "respond"]
    edited_action: dict[str, Any] | None = None  # type=edit 시 필수
    message: str | None = None                   # type=respond 시 필수, type=reject 시 선택

    @model_validator(mode="after")
    def _validate_payload_for_type(self) -> "Decision":
        if self.type == "edit" and self.edited_action is None:
            raise ValueError("Decision(type='edit') requires 'edited_action'")
        if self.type == "respond" and self.message is None:
            raise ValueError("Decision(type='respond') requires 'message'")
        return self
```

### 2.2 `ResumeRequest` (dual-shape)

```python
class ResumeRequest(BaseModel):
    """HiTL resume 요청. Phase 2 transition: 두 형식 양쪽 수용.

    - decisions: 표준 (Phase 2 신규). LangChain HITLResponse 호환.
    - response: legacy (@deprecated, Phase 3에서 제거). 단일 respond decision으로 변환.

    한 요청에 두 필드 모두 들어오면 표준(decisions)만 채택하고 legacy는 버린다.
    둘 다 None이면 422.
    """
    decisions: list[Decision] | None = None
    response: str | list[str] | dict[str, Any] | None = None  # @deprecated

    @model_validator(mode="after")
    def _at_least_one(self) -> "ResumeRequest":
        if self.decisions is None and self.response is None:
            raise ValueError("ResumeRequest requires either 'decisions' or 'response'")
        return self
```

### 2.3 검증 규칙 (단정 결정)

| 케이스 | 결과 |
|---|---|
| `{"decisions": [...]}` | 표준 경로 진입. `response` 무시 (None). |
| `{"response": ...}` | legacy 경로 진입. router가 단일 respond decision으로 변환. |
| `{"decisions": [...], "response": ...}` | **표준 우선, legacy 무시.** 422 거절하지 않음 (transition 관용). 로깅으로만 기록. |
| `{}` 또는 둘 다 None | **422** (`_at_least_one` validator가 reject). |
| `decisions: []` (빈 배열) | router가 그대로 `Command(resume={"decisions": []})` 송신 — 미들웨어가 ValueError 발생 (decisions_len ≠ interrupt_count). 검증은 미들웨어 레이어에 위임. |

### 2.4 legacy → 표준 변환 규칙 (단정 결정)

router의 resume_message에서 `data.decisions is None and data.response is not None`일 때:

```python
def _legacy_response_to_decisions(response: str | list[str] | dict) -> list[dict]:
    """legacy `response` 필드를 단일 respond Decision으로 변환.

    - str: 그대로 message
    - list[str]: ", ".join(...) 으로 단일 문자열 (multi-select 응답)
    - dict: json.dumps(..., ensure_ascii=False) 으로 직렬화
      (중첩 객체 응답 — 자체 ask_user의 historical edge case)
    """
    if isinstance(response, str):
        message = response
    elif isinstance(response, list):
        message = ", ".join(str(item) for item in response)
    else:  # dict
        import json
        message = json.dumps(response, ensure_ascii=False)
    return [{"type": "respond", "message": message}]
```

**근거**: legacy `response`는 자체 `ask_user`의 free-text/single-select/multi-select 답변 또는 builder의 dict 응답이었다. 표준 미들웨어의 `respond` decision이 의미상 동일 — synthetic ToolMessage(success)를 모델에게 전달.

---

## 3. Backend Resume 페이로드 (Command)

**파일**: `backend/app/routers/conversations.py:813-833` → `backend/app/agent_runtime/executor.py:resume_agent_stream`

표준 미들웨어가 정확히 기대하는 dict shape:

```python
from langgraph.types import Command

# router에서 변환 완료된 decisions (list[dict]) 를 그대로 송신
resume_payload: dict = {"decisions": [
    # 각 dict는 LangChain Decision TypedDict와 정확히 매칭
    {"type": "approve"},
    {"type": "edit", "edited_action": {"name": "send_email", "args": {...}}},
    {"type": "reject", "message": "..."},
    {"type": "respond", "message": "..."},
]}

# executor 또는 streaming 진입 직전:
async for chunk in agent.astream(Command(resume=resume_payload), config=config, ...):
    ...
```

**Decision dict 직렬화 규칙**:
- `Decision.model_dump(exclude_none=True)` 사용 — `None` 필드는 LangChain TypedDict에 키 자체를 넣지 않음 (`NotRequired` 호환).
- `edited_action`은 dict 그대로 (Pydantic이 검증). LangChain이 `{"name", "args"}` 키 둘 다 요구.

**resume_agent_stream 시그니처**:
- 현재: `resume_agent_stream(cfg, response, ...)` — `response`는 임의 타입.
- Phase 2: 시그니처 보존 (변수명도 그대로). router가 dict (`{"decisions": [...]}`)로 변환해서 넘긴다. executor 내부에서 `Command(resume=response)` 호출은 동일.
- 의미 변경: `response` 인자가 이제 항상 `dict[str, Any]` (정확히는 `{"decisions": list[dict]}`). 타입힌트는 `dict[str, Any]`로 좁혀도 되고 `Any`로 두어도 무방 (Phase 3에서 좁힘).

---

## 4. INTERRUPT SSE Event Payload — Dual Emit 규칙

**파일**: `backend/app/agent_runtime/streaming.py:331-367`

### 4.1 표준 chunk (신규)

```typescript
{ event: 'interrupt', data: {
    interrupt_id: string,                       // ★ 표준 chunk에도 동봉 (안전망)
    action_requests: [
      { name: string, args: Record<string, unknown>, description?: string }
    ],
    review_configs: [
      { action_name: string, allowed_decisions: ('approve'|'edit'|'reject'|'respond')[] }
    ]
}}
```

**`interrupt_id` 동봉 결정 (단정)**: ADR-012 §4의 표준 형식에는 명시되어 있지 않다. 그러나 Phase 2 transition 안전망으로 **표준 chunk에도 `interrupt_id`를 함께 emit**한다 — frontend가 (a) legacy chunk와의 dedup, (b) stale 검증(`lastInterruptIdRef`)을 끊김 없이 유지하기 위함이다. progress.txt §"Gotchas (Phase 2)" 참조. Phase 3에서 legacy 제거 시 함께 제거 검토 (단, frontend stale 검증을 별도 correlation 필드로 대체 후).

### 4.2 legacy chunk (보존)

```typescript
{ event: 'interrupt', data: {
    interrupt_id: string,
    value: { type?: string, question?: string, options?: string[], message?: string, ... }
}}
```

기존 `streaming.py:349-357` 코드 그대로 유지.

### 4.3 dual emit 순서 / 갯수 (단정)

| 항목 | 결정 |
|---|---|
| **순서** | **표준 먼저 → legacy 나중.** frontend가 표준을 처리한 interrupt_id는 set에 기록 → 동일 ID의 legacy chunk는 무시. |
| **갯수** | 한 task의 한 interrupt당 **정확히 두 chunk** (표준 1 + legacy 1). multi-action(여러 tool_call)은 표준 미들웨어가 `action_requests: [...]` 한 배열로 묶어 발행하므로 **한 interrupt = 한 묶음 = 두 chunk**. tool_call 갯수만큼 chunk가 늘어나지 않음. |
| **공통 interrupt_id** | 두 chunk가 **동일 `interrupt_id`** 값을 갖는다. source: `str(intr.ns)` (현 코드와 동일). 이 ID로 frontend가 dedup. |

### 4.4 표준 chunk 페이로드 source (단정)

`streaming.py`의 `agent.aget_state(config)` 결과 `task.interrupts[*]`에서 추출:

```python
for task in state.tasks:
    for intr in task.interrupts:
        intr_id = str(getattr(intr, "ns", ""))
        intr_value = intr.value if isinstance(intr.value, dict) else None

        # 표준 chunk: intr.value가 LangChain HITLRequest TypedDict
        # ({"action_requests": [...], "review_configs": [...]}) 일 때 그대로 사용.
        # 자체 ask_user.py가 발행한 interrupt는 이 shape이 아니므로 표준 chunk를 emit하지 않는다.
        if intr_value and "action_requests" in intr_value and "review_configs" in intr_value:
            yield emit(event_names.INTERRUPT, {
                "interrupt_id": intr_id,
                "action_requests": intr_value["action_requests"],
                "review_configs": intr_value["review_configs"],
            })

        # legacy chunk: 항상 emit (transition window).
        # ask_user 자체 interrupt + 표준 미들웨어 interrupt 둘 다 동일하게 처리.
        yield emit(event_names.INTERRUPT, {
            "interrupt_id": intr_id,
            "value": intr_value if intr_value is not None else {"message": str(intr.value)},
        })
```

**중요**: 자체 `ask_user.py` (Phase 4까지 보존)는 표준 wire의 `action_requests/review_configs` shape을 발행하지 않으므로 **표준 chunk가 emit되지 않는다**. 이 경우 frontend는 legacy chunk 단독으로 도착 → 기존 어댑터(adapter to standard) 또는 기존 onInterrupt 경로로 처리. 회귀 0.

### 4.5 fallback 분기 (`was_interrupted=True` + `aget_state` 실패)

`streaming.py` 의 except 블록. state 조회 실패라 표준 shape을 구성할 정보가 없으므로 **legacy chunk만 emit**, `interrupt_id=""` (빈 문자열).

```python
except Exception:
    logger.warning("aget_state failed (interrupt check)", exc_info=True)
    if was_interrupted:
        yield emit(event_names.INTERRUPT, {
            "interrupt_id": "",
            "value": {"message": "Interrupt detected but state unavailable"},
        })
```

frontend는 `action_requests` 키 부재로 legacy 경로를 채택해 fallback 메시지를 1회 노출 (5절 참조).

---

## 5. Frontend 처리 규칙

**파일**: `frontend/src/lib/chat/use-chat-runtime.ts:case 'interrupt'`, `frontend/src/lib/types/index.ts`

### 5.1 InterruptPayload union (types)

```ts
// 표준 chunk
export interface ActionRequest {
  name: string
  args: Record<string, unknown>
  description?: string
}
export type DecisionType = 'approve' | 'edit' | 'reject' | 'respond'
export interface ReviewConfig {
  action_name: string
  allowed_decisions: DecisionType[]
}
export interface StandardInterruptPayload {
  interrupt_id: string
  action_requests: ActionRequest[]
  review_configs: ReviewConfig[]
}

// legacy chunk (기존, 보존)
export interface LegacyInterruptPayload {
  interrupt_id: string
  value: Record<string, unknown>
}

export type InterruptPayload = StandardInterruptPayload | LegacyInterruptPayload

// Decision (resume 송신용)
export interface Decision {
  type: DecisionType
  edited_action?: { name: string; args: Record<string, unknown> }
  message?: string
}

export interface ResumeDecisionsRequest {
  decisions: Decision[]
}
```

### 5.2 분기 + dedup (use-chat-runtime.ts)

```ts
// 모듈/컴포넌트 스코프 ref
const handledStandardInterruptIdsRef = useRef<Set<string>>(new Set())

// case 'interrupt': 분기 로직 (표준 우선, legacy fallback)
case 'interrupt': {
  setIsRunning(false)
  const data = event.data as InterruptPayload
  const intrId = data.interrupt_id
  if (intrId) lastInterruptIdRef.current = intrId

  // 표준 chunk: action_requests 키 존재 + 비어있지 않을 때만 표준 처리
  if (
    'action_requests' in data &&
    Array.isArray(data.action_requests) &&
    data.action_requests.length > 0
  ) {
    handledStandardInterruptIdsRef.current.add(intrId)
    onStandardInterrupt?.(data)
    break
  }

  // legacy chunk: 동일 interrupt_id의 표준이 이미 처리됐으면 무시 (dedup)
  if (intrId && handledStandardInterruptIdsRef.current.has(intrId)) {
    break
  }

  // legacy 단독 도착 (회귀 0 보장 경로)
  onInterrupt?.(data as LegacyInterruptPayload)
  break
}
```

**규칙 단정**:
- 표준 chunk 처리는 **`action_requests`가 비어있지 않을 때만**. 빈 배열 표준 chunk(4.5절 fallback)는 표준 처리에서 skip → 같은 ID의 legacy chunk가 도착 시 그쪽이 채택됨.
- `handledStandardInterruptIdsRef`는 **컴포넌트 라이프사이클 동안 누적**. 새 conversation 진입 시 컴포넌트 remount로 자연 리셋(이미 다른 영역에서 그런 패턴). 회귀 가드: streamGuard와 별도 ref로 유지(streamGuard는 stream-단위 reset).
- `lastInterruptIdRef.current = intrId`는 두 chunk 모두에서 갱신 가능 — 같은 값이라 멱등. resume 시 stale 검증 경로 그대로.
- Phase 3에서 legacy chunk 처리 + handledStandardInterruptIdsRef 둘 다 제거.

### 5.3 multi-action 처리 (Phase 2 결정 — 단정)

표준 `action_requests.length >= 2`일 때:
- frontend는 배열을 **순차 카드 + 일괄 확정 버튼** UX로 렌더 (M2 저커버그 책임). 자세한 컴포넌트 디자인은 M2 스토리에서 결정.
- 사용자가 N개 카드 모두 결정 → "전체 확정" → 단일 `streamResumeDecisions(conversationId, decisions)` 호출. backend가 `Command(resume={"decisions": [N개]})` 한 번에 송신.
- 길이 1이면 기존 단일 카드 UX와 동치.

---

## 6. stream-resume.ts 송신 형식

**파일**: `frontend/src/lib/sse/stream-resume.ts`

### 6.1 신규 함수 (표준)

```ts
export async function* streamResumeDecisions(
  conversationId: string,
  decisions: Decision[],
  signal?: AbortSignal,
  options?: StreamSSEPostOptions,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/conversations/${conversationId}/messages/resume`,
    { decisions },                              // 표준 body
    signal,
    'content_delta',
    options,
  ) as AsyncGenerator<SSEEvent>
}
```

### 6.2 기존 함수 (legacy 어댑터로 보존)

```ts
// 기존 시그니처 그대로. body { response } 송신.
// 자체 ask_user / 기존 호출자(미수정 경로) 호환용.
export async function* streamResume(
  conversationId: string,
  response: unknown,
  signal?: AbortSignal,
  options?: StreamSSEPostOptions,
): AsyncGenerator<SSEEvent> { /* ... 기존과 동일 ... */ }
```

### 6.3 HiTLContext 노출

```ts
export interface HiTLContextValue {
  /** 표준 (Phase 2 신규). decisions[] 송신 → /resume. */
  onResumeDecisions: (decisions: Decision[], displayText?: string) => Promise<void>
  /** legacy (@deprecated, Phase 3 제거). 단일 respond/임의값 송신. */
  onResume: (response: unknown, displayText?: string) => Promise<void>
}
```

표준 사용처(ApprovalCard 신규 처리 경로)는 `onResumeDecisions` 호출. legacy 사용처(자체 UserInputUI)는 기존 `onResume` 그대로 — 이 경우 backend router가 legacy → 표준 변환을 수행.

---

## 7. `allowed_decisions` 기본값 (코드 검증 결과)

**소스**: `backend/.venv/lib/python3.13/site-packages/langchain/agents/middleware/human_in_the_loop.py:215-220`

```python
for tool_name, tool_config in interrupt_on.items():
    if isinstance(tool_config, bool):
        if tool_config is True:
            resolved_configs[tool_name] = InterruptOnConfig(
                allowed_decisions=["approve", "edit", "reject", "respond"]
            )
    elif tool_config.get("allowed_decisions"):
        resolved_configs[tool_name] = tool_config
```

**단정 결정**:
- `interrupt_on={"tool_name": True}` → `allowed_decisions = ["approve", "edit", "reject", "respond"]` (네 가지 모두).
- `interrupt_on={"tool_name": False}` → entry 자체 무시 (auto-approve).
- `interrupt_on={"tool_name": {"allowed_decisions": [...]}}` → 명시 값 그대로.

**Frontend 영향**: `True`로 설정된 도구는 4 액션이 모두 허용된다 — `ApprovalCard`가 `respond` 버튼도 노출해야 함 (현재는 ask_user 도구에만 노출). 이 UX 결정은 M2 저커버그 책임 — 본 wire contract는 `review_configs[i].allowed_decisions` 배열을 그대로 노출하므로 frontend가 그 배열을 보고 버튼을 동적으로 렌더하면 된다.

---

## 8. Phase 1 산출물 보존 (수정 금지)

본 PR은 다음 영역을 **일체 수정하지 않는다**:

| 파일 | 보존 사유 |
|---|---|
| `backend/app/agent_runtime/middleware_registry.py` | Phase 1 주석(자동 주입 회피 + executor 명시 인스턴스화 경로) 보존. |
| `backend/app/agent_runtime/executor.py:_prepare_agent` 시그니처 + 트리거 차단 동작 (`include_ask_user=False` → `interrupt_on=None`) | Phase 1의 핵심 안전장치. Phase 2는 resume 경로(`resume_agent_stream`)만 변경 가능, `_prepare_agent`는 시그니처/동작 모두 보존. |
| `backend/tests/test_hitl_middleware.py` (5건) | Phase 1 회귀 가드. `test_hitl_middleware_instance_injected_when_interrupt_on_provided`, `test_hitl_middleware_not_injected_in_trigger_mode`, `test_hitl_middleware_per_tool_policy_applied`, `test_hitl_middleware_auto_extraction_from_write_keywords`, `test_deepagents_interrupt_on_param_is_none_when_explicit_instance` 모두 PASS 상태 유지. |
| `backend/app/agent_runtime/tools/ask_user.py` | Phase 4까지 보존. `interrupt()` 직접 호출 그대로. |
| `backend/app/agent_runtime/builder_v3/**` | Phase 5까지 보존 — Builder v3는 자체 native interrupt 패턴(`pending_tool_call_id`)을 유지. wire format 통일은 Phase 5 별도 트랙. |

---

## 9. 파일별 변경 명세 (M1·M2 즉시 작업 시작용)

### Backend (4 파일, 젠슨 M1)

| 파일 | 라인 / 위치 | 변경 요약 |
|---|---|---|
| `backend/app/schemas/conversation.py` | 45-46 (현 `ResumeRequest`) | `Decision` 모델 신규 + `ResumeRequest{decisions?, response?}` dual-shape + `model_validator` 검증. 본 문서 §2 정확히 반영. |
| `backend/app/routers/conversations.py` | 813-833 (`resume_message`) | `data.decisions` 우선 분기. 없으면 `_legacy_response_to_decisions(data.response)`로 변환. 둘 다 dict `{"decisions": [...]}`로 통일해 `resume_agent_stream(cfg, payload, ...)` 호출. |
| `backend/app/agent_runtime/streaming.py` | 331-367 (`GraphInterrupt` catch + `aget_state` 분기) | 표준 chunk(action_requests/review_configs/interrupt_id) + legacy chunk(interrupt_id/value) **순서대로 dual emit**. fallback 분기(except)도 두 chunk 모두 emit, `interrupt_id=""`. 본 문서 §4 정확히 반영. |
| `backend/app/agent_runtime/executor.py` | `resume_agent_stream` 시그니처 | **시그니처 보존**. 두 번째 인자(`response` → `payload`로 rename 가능, 의미는 dict `{"decisions": [...]}`). 내부의 `Command(resume=...)` 호출은 그대로. `_prepare_agent` 일체 미수정 (Phase 1 보존). |

### Frontend (5 파일, 저커버그 M2)

| 파일 | 위치 | 변경 요약 |
|---|---|---|
| `frontend/src/lib/types/index.ts` | 264-300 (interrupt variant) | `StandardInterruptPayload` + `LegacyInterruptPayload` + `InterruptPayload` union + `Decision` + `ActionRequest` + `ReviewConfig` + `DecisionType` + `ResumeDecisionsRequest`. 기존 `InterruptPayload` 인터페이스는 `LegacyInterruptPayload`로 rename 후 `InterruptPayload` 자리는 union이 차지. |
| `frontend/src/lib/chat/use-chat-runtime.ts` | 360-367 (`case 'interrupt'`), 98-99 (콜백 prop), 137-138 (refs) | `handledStandardInterruptIdsRef` 신규. `'action_requests' in data` 체크로 표준/legacy 분기. 표준이면 `onStandardInterrupt?.(data)`, dedup. legacy fallback은 `onInterrupt?.(data)` 그대로. props에 `onStandardInterrupt?: (payload: StandardInterruptPayload) => void` 추가. |
| `frontend/src/lib/sse/stream-resume.ts` | 신규 export | `streamResumeDecisions(conversationId, decisions[], ...)` 신규. 기존 `streamResume(conversationId, response, ...)` 시그니처 보존. |
| `frontend/src/lib/chat/hitl-context.ts` | `HiTLContextValue` | `onResumeDecisions(decisions: Decision[], displayText?: string)` 추가. `onResume`는 그대로. `useHiTL` 변경 없음. |
| `frontend/src/messages/ko.json` | `chat.approval.*` | `chat.approval.respond`, `chat.approval.allActionsCompleted`, `chat.approval.confirmAll`, `chat.approval.actionN` 등 multi-action UX 라벨. 정확한 키/문구는 M2 저커버그 결정. |

추가(저커버그 재량): `frontend/src/components/chat/{user-input-ui,approval/*}.tsx` — wire 어댑터 적용. 컴포넌트 자체는 4 액션 이미 지원하므로 props 어댑팅만.

---

## 10. 검증 매트릭스 (Phase 2 PR done-when)

### Backend (M1 — 젠슨)

| 명령 | 게이트 |
|---|---|
| `cd backend && uv run ruff check .` | exit 0 (clean) |
| `cd backend && uv run pyright app/` | 0 errors / 0 warnings |
| `cd backend && uv run pytest tests/` | **826 (Phase 1 baseline) + 신규(M3) PASS, 회귀 0** |
| `cd backend && uv run alembic upgrade head` | 머지 가능 (Phase 2는 마이그레이션 없음, sanity check) |

### Frontend (M2 — 저커버그)

| 명령 | 게이트 |
|---|---|
| `cd frontend && pnpm lint` | 0 errors (기존 pre-existing warning 허용) |
| `cd frontend && pnpm test --run` | 기존 PASS + 신규(M3) PASS, 회귀 0 |
| `cd frontend && pnpm build` | TypeScript clean, 16 routes OK |

### 신규 회귀 테스트 (M3 — 베조스)

| 테스트 파일 | 시나리오 → contract section |
|---|---|
| `backend/tests/test_hitl_phase2_wire.py` (신규) | (a) `decisions: [...]` 송신 → `Command(resume={"decisions": [...]})` 검증 → §3 |
| 동상 | (b) `response: "foo"` 송신 → 단일 respond decision 변환 검증 → §2.4 |
| 동상 | (c) `response: ["a","b"]` → ", ".join → respond.message 검증 → §2.4 |
| 동상 | (d) `response: {"x":1}` → json.dumps → respond.message 검증 → §2.4 |
| 동상 | (e) `{}` 빈 body → 422 → §2.3 |
| 동상 | (f) `{"decisions": [...], "response": "x"}` → 표준 우선, legacy 무시 → §2.3 |
| 동상 | (g) streaming GraphInterrupt → 표준 + legacy 두 chunk emit, 순서 검증, 동일 interrupt_id → §4.3 |
| 동상 | (h) `was_interrupted=True` + `aget_state` 실패 → 두 chunk emit, `interrupt_id=""` → §4.5 |
| `frontend/src/lib/chat/__tests__/use-chat-runtime-hitl.test.ts` (신규 또는 보강) | (a) 표준 payload 도착 → `onStandardInterrupt` 호출, multi-action 큐 → §5.2, §5.3 |
| 동상 | (b) legacy payload 도착 → `onInterrupt` 호출 (회귀 0) → §5.2 |
| 동상 | (c) 표준 → legacy 순으로 도착 (같은 interrupt_id) → 표준만 1회 처리, legacy dedup → §4.3, §5.2 |
| 동상 | (d) `streamResumeDecisions` body shape 검증 (`{decisions:[...]}`) → §6.1 |
| 동상 | (e) `streamResume` body shape 검증 (`{response}`) — 회귀 가드 → §6.2 |

### M4 — 통합 검증 (사티아)

위 backend + frontend + 신규 테스트 전부 PASS + HANDOFF.md를 Phase 3 사전 정보로 갱신.

---

## 부록 A. 트리거 모드 (Phase 1 보존)

`backend/app/agent_runtime/executor.py:_prepare_agent`의 `include_ask_user=False` indicator에 의해 트리거(스케줄러/`execute_agent_invoke`) 경로는 `interrupt_on=None`으로 강제되어 `HumanInTheLoopMiddleware` 인스턴스 자체가 주입되지 않는다. 따라서 트리거 경로에서는 본 contract의 INTERRUPT 발행이 발생하지 않는다 — Phase 2에서 추가 변경 없음.

## 부록 B. 결정 미해결 항목 (Phase 3+)

본 contract는 Phase 2의 wire 결정만 담는다. 다음은 Phase 3+에서 결정:

- 표준 chunk의 `interrupt_id` 보존 여부 (§4.1 안전망 — Phase 3에서 frontend stale 검증을 별도 correlation 필드로 옮긴 뒤 제거 가능).
- legacy `streamResume` 함수 + `onResume` HiTLContextValue 필드 제거 시점 (Phase 3).
- multi-action UI의 디자인 (carousel vs accordion vs sequential) — M2 저커버그 결정 후 ADR 또는 design-doc로 분리.
- `respond` 버튼을 모든 표준 도구(`interrupt_on={"tool": True}` 케이스)에 노출할지 — `review_configs[i].allowed_decisions` 그대로 따르는 단순 정책으로 시작 (M2 결정).
