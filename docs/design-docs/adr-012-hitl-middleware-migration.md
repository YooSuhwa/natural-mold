# ADR-012: HiTL — 자체 구현에서 LangChain `HumanInTheLoopMiddleware` 로 마이그레이션

## 상태: Phase 1~4 완료, Phase 5 진행 중 (Builder v3 wire 통일)

관련 문서:
- 마일스톤 진행: `HANDOFF.md` (루트)
- 분석 PR: feature/hitl-analysis-and-plan (본 PR)

## Phase 4 결정 회고 (2026-05-06)

옵션 B (ask_user retire) 를 한 차례 시도 후 사용자 검증에서 **자연어 "되물어보기" UX 손실**을 발견하고 즉시 revert. 옵션 A (보존) 가 최종 결정.

핵심 인사이트:
- `HumanInTheLoopMiddleware` = "위험 도구 실행 전 승인 게이트". 도구 호출 시점에만 발동.
- `ask_user` = "사용자 자연어 질문 도구". LLM이 모호한 입력을 받았을 때 사용자에게 옵션을 제시.
- **두 책임은 직교** — 미들웨어가 ask_user를 대체할 수 없다. 옵션 B가 단순화 효과는 있지만 UX 시나리오를 통째로 잃는다.

§5 옵션 B의 표면 사유 ("도구 description의 implicit prompt 오염")는 사실이지만, 그 비용이 UX 손실보다 작다는 트레이드오프 계산이 잘못이었음. 향후 누군가 다시 옵션 B를 시도하지 않도록 본 회고를 명시.

---

## Phase 5 회고 — ADR-012 마이그레이션 종료 (2026-05-06)

Phase 0~5 모두 완료 시 ADR-012 5단계 마이그레이션 전체 종료. Phase 5 진입 시점의 핵심 결정:

- **Router-only 어댑터**: graph 본체 (`builder_v3/graph.py`, `state.py`, `phase{2,3,4,5,7,8}*.py`) 변경 0. backend router/services 가 `decisions_to_builder_response` helper 로 표준 → builder native shape 변환. frontend `decisionToBuilderResponse` 어댑터 (PR #135) 의 책임을 그대로 backend 로 이전 — 동작 변경 0, dual-wire 제거.
- **Phase 6 JSON.parse fallback**: image_choice / image_approval 의 string 분기에 JSON.parse 시도만 추가 (3-5 라인). 기존 dict/string 분기 우선, JSON string 만 신규 처리 — backward compatible.
- **Clean break**: `BuilderResumeRequest.response` 필드 즉시 제거. Phase 2 dual-path transition 학습 (메인 채팅) 적용 — Builder 는 사용자 영향 범위가 좁아 clean break 안전.
- **어댑터 retire**: PR #135 (-18) + 테스트 (-55, 8 가드 retire). Phase 5 PR 자체에 신규 가드 ≥3건 보전 (helper 매핑, 422, JSON.parse).

핵심 학습:
1. **graph 디렉토리는 단일 책임 유지**. wire 어댑터 / 변환 helper 는 services 레이어가 책임. builder_v3/ 안에 `_resume_adapter.py` 두는 것은 graph state machine 의 응집도를 흐림 — services/builder_service.py 안에 helper 두는 것이 올바른 모듈 경계.
2. **Phase 별 wire 통일 vs graph 보존 트레이드오프**: 메인 채팅은 표준 미들웨어 마이그레이션 (graph 행동 변경 포함) 가치 컸음. Builder 는 8-phase deterministic state machine 패턴이라 router-only 어댑터로 wire 만 통일하는 것이 옳음 — 직교 관계 보존.
3. **clean break 가드의 가치**: `test_resume_rejects_legacy_response_field_422` 같은 가드는 단순 negative test 가 아니라 "두 wire 형식의 공존 의도가 없다" 는 ADR 결정을 코드로 잠그는 디자인 락. 향후 누군가 호환성 명목으로 dual-shape 다시 추가하는 것을 차단.

---

## 맥락

현재 메인 채팅의 HiTL (Human-in-the-Loop) 은 deep agents 도입 이전에 만들어진 자체 구현. 세 갈래로 분산:

1. `tools/ask_user.py` — LangGraph `interrupt()` 직접 호출하는 special tool
2. `streaming.py:331-367` — `GraphInterrupt` catch + 자체 SSE INTERRUPT event emit
3. `routers/conversations.py:813-833` — `POST /messages/resume` + `Command(resume=response)`

미들웨어는 등록만 되고 인스턴스화는 명시 제외 (`middleware_registry.py:419`). `executor.py:477-493` 가 `interrupt_on` dict 만 추출해 자체 처리.

문제:
1. **트리거 모드 무용** — `ask_user` 호출되면 사용자 응답 없이 영원히 멈춤
2. **도구별 정책 불가** — all-or-nothing (ask_user 호출 = 무조건 사람 대기)
3. **Multi tool_call 분산** — 한 AIMessage 의 N tool_call → N interrupt → 사용자 N번 클릭
4. **deep agents 의 SubAgent 상속 / built-in tool 적용 활용 불가**

---

## 결정

### 1. 메인 채팅만 표준 `HumanInTheLoopMiddleware` 로 마이그레이션

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-5",
    tools=[send_email, write_file, ask_user],
    interrupt_on={
        "send_email": True,
        "write_file": {"allowed_decisions": ["approve", "reject"]},
        "ls": False,
        "ask_user": {"allowed_decisions": ["respond"]},  # 자체 도구도 표준 경로 wrap
    },
    checkpointer=postgres_saver,
)
```

근거:
- 트리거 모드: `interrupt_on={"ask_user": False}` 로 자동 승인 가능
- 도구별 정책: 위험도별 차등 (PRD 의 "위험 액션 전 승인" 정확 매칭)
- Multi tool_call 일괄: 한 AIMessage 의 모든 tool_call → 한 interrupt
- LangChain 1.x 안정 + DeepAgents 자동 주입

### 2. Builder v3 는 자체 패턴 유지

**근거** (분석 결과):
- Builder v3 는 8-phase deterministic state machine (`backend/app/agent_runtime/builder_v3/graph.py`)
- 노드가 LLM 호출 사이클이 아닌 **직접** `interrupt()` 호출 — `propose + wait` 분리 (LangGraph 권장 pattern)
- Phase 별 dialog flow 는 "tool_call interrupt" 메타포에 안 맞음
- Stale interrupt 검증 (`pending_tool_call_id`) 도 long-running 멀티스텝 전용 — 일반 채팅 불필요

→ **메인 채팅과 직교 관계.** Builder v3 는 LangGraph native interrupt 사용이 맞음. 표준 미들웨어로 통합 X. wire format 만 선택적으로 통일 가능.

### 3. ResumeRequest payload 표준 형식

```python
# 신규 (Phase 2)
class ResumeRequest(BaseModel):
    decisions: list[Decision]  # length === interrupt_on tool_call count
    # transition 동안: response 필드도 받아 단일 respond decision 으로 변환
    response: str | list[str] | dict | None = None  # @deprecated

class Decision(BaseModel):
    type: Literal["approve", "edit", "reject", "respond"]
    edited_action: dict | None = None  # type=edit
    message: str | None = None         # type=reject | respond
```

### 4. INTERRUPT SSE event payload 표준화

```typescript
// 표준 (Phase 2)
{ event: 'interrupt', data: {
    action_requests: [{ name: string, args: Record<string, unknown>, description?: string }],
    review_configs: [{ action_name: string, allowed_decisions: ('approve'|'edit'|'reject'|'respond')[] }]
} }
// 기존 (transition 동안 dual emit 가능)
{ event: 'interrupt', data: { interrupt_id: string, value: { type: 'ask_user', question: string, options?: string[] } } }
```

### 5. `ask_user` 도구는 보존 (옵션 A)

옵션 A (선택됨): `ask_user` 도구 그대로 유지 + 표준 미들웨어가 `interrupt_on={"ask_user": True}` 로 wrap.
- LLM prompt / 도구 description 영향 없음
- 자체 `interrupt()` 호출은 미들웨어 호출과 양립 (미들웨어가 tool_call 단계에서 interrupt 발행, ask_user 자체는 빈 도구로 retire 가능)
- 마이그레이션 후 단계적으로 ask_user 단순화

옵션 B (보류): `ask_user` 완전 제거. LLM prompt 변경 필요 + 회귀 위험.

### 6. Frontend UI 활용 — 4 액션은 이미 구현됨

분석 결과:
- `UserInputUI` (ask_user) — `respond` 액션 (free text + single/multi select)
- `ApprovalCard` (request_approval) — `approve` / `reject` / `edit` 모두 지원
- `HiTLContext` + `onResume` callback 패턴

→ **Phase 2 의 UI 작업은 컴포넌트 신규가 아닌 wire 어댑터** + multi-action 큐 처리.

### 7. Multi-action 일괄 큐 처리 (Phase 2)

현재 `consumeStream` 의 `case 'interrupt'` 는 단일 interrupt 가정 (`onInterrupt(payload)`).
표준 미들웨어는 한 AIMessage 의 모든 tool_call 묶음 → frontend 가 배열 큐로 처리.

### 8. APScheduler 트리거는 명시 차단

`execute_agent_invoke` 경로 (트리거) 는 사용자 비동기 환경 — interrupt 불가.
`interrupt_on` config 를 트리거 호출 시 모두 `False` 로 override (또는 미들웨어 자체 미주입).

---

## 마이그레이션 단계 (Phase 별 PR)

### Phase 0 — 선행 분석 + ADR (본 PR)
- 코드 변경 없음. ADR-012 + HANDOFF 갱신만.

### Phase 1 — Backend 인프라 (사용자 무영향, ~150 라인)
**Done-when**: 표준 미들웨어 인스턴스가 deep agent 에 주입되지만 SSE wire 는 자체 형식 유지 (dual-path)

- `executor.py`: `interrupt_on` dict → `HumanInTheLoopMiddleware(interrupt_on=...)` 인스턴스 생성, deep agent 에 추가
- `middleware_registry.py`: `human_in_the_loop` 제외 목록 정리 — 미들웨어 정상 인스턴스화
- 신규 단위 테스트: 표준 미들웨어 도구별 interrupt_on 적용 / 트리거 모드 자동 승인

**파일**: `backend/app/agent_runtime/{executor.py, middleware_registry.py}` + 신규 `tests/test_hitl_middleware.py`

### Phase 2 — Wire Format 통합 (사용자 영향, dual-path transition, ~400 라인)
**Done-when**: INTERRUPT event 표준 형식 + ResumeRequest `decisions: [...]` 형식 둘 다 작동, frontend 4 액션 + multi-action 큐 지원

Backend:
- `schemas/conversation.py`: `ResumeRequest{decisions, response?}` (dual-shape)
- `routers/conversations.py:resume_message`: `decisions` → `Command(resume={"decisions": [...]})`. `response` 가 들어오면 단일 respond decision 으로 변환 (transition)
- `streaming.py`: GraphInterrupt catch 시 표준 `{action_requests, review_configs}` 형식 emit. 기존 자체 형식도 dual emit (transition)

Frontend:
- `lib/types/index.ts`: `SSEEventType` 의 interrupt variant 에 표준 + 기존 두 형식 union
- `lib/chat/use-chat-runtime.ts:case 'interrupt'`: 표준 payload 처리 (multi-action 큐)
- `lib/sse/stream-resume.ts`: `{decisions: [...]}` 형식으로 송신
- `HiTLContext` / `useHiTL`: 배열 처리 + 어댑터
- `messages/ko.json`: `chat.approval.respond`, `chat.approval.allActionsCompleted` 등 라벨 추가
- 회귀 테스트 — 표준 형식 + 기존 형식 둘 다 처리

### Phase 3 — Transition 종료 (~80 라인)
**Done-when**: dual-path 제거, 표준 형식만 유지

- backend ResumeRequest 의 `response` 필드 제거
- frontend 의 기존 `{interrupt_id, value}` 처리 코드 제거
- streaming.py 의 자체 INTERRUPT emit 제거 (표준 미들웨어 발행만)

### Phase 4 — `ask_user` 검토 (옵션, ~30 라인)
**Done-when**: ask_user 도구의 의존성 평가 완료, 단순화 또는 retire 결정

- ask_user 의 LLM prompt 영향 분석
- 표준 미들웨어로 충분히 대체 가능하면 도구 제거
- 옵션 선택 UX 보존 필요 시 `ask_user` 의 description 조정

### Phase 5 — Builder v3 wire format 통일 (~150 라인)
**Done-when**: Builder v3 의 ResumeRequest 도 표준 `decisions: list[Decision]` 형식 수신, frontend `decisionToBuilderResponse` 어댑터 retire, graph + state + 8 phase 노드 (phase6 외) 변경 0, 회귀 가드 ≥3건 PASS

**사용자 결정 (2026-05-06)**: Router-only 어댑터 + image_choice JSON.parse fallback + Clean break (dual-path 없음)

작업 항목:
- `decisions_to_builder_response(decisions)` helper 신규 — `backend/app/services/builder_service.py` (graph 디렉토리는 graph/state/nodes 단일 책임 유지, wire 어댑터는 services 가 책임)
- `routers/builder.py` `BuilderResumeRequest{decisions: list[Decision]}` clean break (legacy `response` 필드 제거)
- `routers/builder.py` `resume_message` handler — helper 호출 후 `Command(resume=...)` 전달
- `phase6_image.py` (choice + approval) string JSON.parse fallback 3-5 라인 — backward compatible (기존 dict/string 분기 우선, JSON string 만 추가 처리)
- frontend `lib/chat/builder-resume-adapter.ts` 삭제 (-18) + `__tests__/builder-resume-adapter.test.ts` 삭제 (-55, 8 가드 retire — Phase 5 PR 자체에 회귀 가드 ≥3건 신규로 보전)
- `use-chat-runtime.ts:onResumeDecisions` 어댑터 호출 제거, `ResumeFn` 시그니처 `decisions: Decision[]` 로 갱신
- `stream-builder-resume.ts` 시그니처 + POST body `{decisions, display_text, interrupt_id}`

회귀 가드 (≥3건, 신규):
- `test_resume_accepts_standard_decisions` — 표준 wire 200
- `test_resume_rejects_legacy_response_field_422` — clean break 가드
- `test_decisions_to_builder_response_mapping` — helper 단위
- `test_phase6_choice_accepts_json_string` — JSON.parse fallback 회귀 방지

**보존 (수정 금지)**: `builder_v3/graph.py`, `state.py`, `phase{2,3,4,5,7,8}*.py`, `_helpers.parse_approval_response` (dict|str 호환), `pending_tool_call_id` stale 검증.

---

## 위험 + 완화

| 위험 | 완화 |
|------|------|
| Wire format 변경 = breaking change | dual-path transition window — 한 PR 에서 둘 다 받기, 4 PR 후 제거 |
| Multi-action UI 신규 디자인 부재 | `ApprovalCard` 가 이미 단일 액션 지원 — 배열 렌더링 + 일괄 확정 버튼 추가만 |
| `ask_user` LLM prompt 변경 시 회귀 | Phase 4 까지 ask_user 보존, 충분한 회귀 테스트 후 결정 |
| Builder v3 graph 영향 | Router-only 어댑터로 graph + state + 대부분 노드 변경 0. phase6 image_choice/approval 만 backward-compatible JSON.parse fallback 추가 (기존 dict/string 분기 우선, JSON string 만 신규 처리). 회귀 가드 ≥3건으로 매핑 + 422 + JSON.parse 검증. |
| 트리거 모드에서 interrupt 발생 시 hang | 트리거 호출 시 `interrupt_on` config 강제 override, 회귀 테스트 |
| Stale interrupt (오래된 카드 클릭) | Builder 의 `pending_tool_call_id` 패턴을 메인 채팅에도 적용 검토 (Phase 2 내) |

---

## 검증

각 Phase PR 마다:
- `cd backend && uv run alembic upgrade head && uv run ruff check . && uv run pytest tests/ && uv run pyright app/ tests/`
- `cd frontend && pnpm lint && pnpm test --run && pnpm build`

Phase 별 신규 회귀 테스트:
- Phase 1: 도구별 interrupt_on 적용 / 트리거 모드 자동 승인
- Phase 2: 표준 + 기존 wire 양쪽 작동 / multi-action 일괄 처리
- Phase 3: 기존 wire 제거 후 회귀 0
- Phase 4: ask_user retire 시 LLM prompt 회귀
- Phase 5: builder graph state 회귀 0

수동 e2e:
- 일반 도구 (e.g. write_file) 호출 → approve/reject/edit 각 액션 동작
- multi tool_call (e.g. delete_file + send_notification) 동시 emit → 배열 검토 → 일괄 결정
- ask_user 호출 → respond 동작
- 트리거에서 interrupt-가능 도구 호출 → 자동 승인

---

## 결정 근거 요약 (TL;DR)

deep agents 도입 후 자체 HiTL 구현이 표준 미들웨어의 가치 (도구별 정책 / SubAgent 상속 / 트리거 자동 승인 / multi tool_call 일괄) 를 막고 있다. 메인 채팅을 표준 `HumanInTheLoopMiddleware` 로 마이그레이션하면 (a) 트리거 모드에서 HiTL 정책 의미 있게 적용 가능, (b) 도구별 위험도 차등 (PRD 의 핵심 시나리오), (c) 한 AIMessage 의 multi tool_call 사용자 클릭 1번. Builder v3 는 deterministic state machine 패턴이라 직교 관계, 자체 유지가 맞음. 비용은 5 Phase, 약 800 라인, 회귀 위험은 dual-path transition 으로 완화.
