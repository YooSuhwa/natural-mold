# ADR-012: HiTL — 자체 구현에서 LangChain `HumanInTheLoopMiddleware` 로 마이그레이션

## 상태: Phase 4 진행 중 (옵션 B — ask_user retire, 메인 채팅 한정)

관련 문서:
- 마일스톤 진행: `HANDOFF.md` (루트)
- 분석 PR: feature/hitl-analysis-and-plan (본 PR)

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

### 5. `ask_user` 도구 retire (옵션 B 선택, Phase 4)

> **이력**: Phase 1~3 transition 동안은 옵션 A(보존 + 표준 미들웨어 wrap)였으나, Phase 4 진입 시 옵션 B(retire)로 전환. 베조스 의존성 분석 결과 retire 범위는 **메인 채팅 한정**이며, builder_v3 의 native interrupt 패턴은 ADR-012 §Phase 5 영역으로 보존.

**옵션 B 선택 사유**:
1. **표준 미들웨어가 모든 도구의 interrupt 처리를 일원화** — 자체 `ask_user` 도구가 직접 `interrupt()` 호출하는 패턴은 표준 `HumanInTheLoopMiddleware` 와 의미 중복. 미들웨어가 모든 tool_call 단계에서 interrupt 를 발행하므로 자체 도구가 더 이상 필요 없다.
2. **트리거 모드 무용** — HANDOFF 가 이미 지적: `_prepare_agent(include_ask_user=False)` indicator 분기 자체가 트리거에서 hang 을 막기 위한 우회. 도구를 retire 하면 indicator 도 함께 사라져 표준 경로로 단일화.
3. **Prompt 오염 제거** — `ask_user` 의 docstring(L13–28) 이 OpenAI tools 배열에 description 으로 직렬화되어 LLM 입력에 항상 노출된다. system_prompt 에는 직접 언급이 없지만, 도구 카드(name + description) 자체가 모델 행동에 영향을 미치는 implicit prompt. retire 시 description 부담 자체 제거.

**중요 보존 영역** (회귀 위험 최소화 가드):
- `backend/app/agent_runtime/builder_v3/**` — Phase 2 intent 노드(`phase2_intent.py`) + router fallback(`router.py`) 의 native `interrupt({"type":"ask_user", ...})` 발행 패턴 유지. 8-phase deterministic state machine 의 직교 영역(§2 참조).
- `backend/app/agent_runtime/streaming.py:84-117` — ask_user 어댑터 분기는 builder_v3 의 native interrupt 를 표준 `respond` action chunk 로 변환하는 역할. **삭제 X, 주석만 갱신** ("builder_v3 native interrupt 어댑터" 명시).
- `frontend/src/components/chat/tool-ui/user-input-ui.tsx` + `tool-ui-registry.ts:34,51,57` + `frontend/messages/ko.json:558-571` — builder_v3 UI 가 동일 컴포넌트/라벨 사용. 코드 보존, JSDoc/주석에 "Builder v3 전용" 명시.

옵션 A (이전): `ask_user` 도구 보존 + `interrupt_on={"ask_user": True}` wrap. → Phase 1~3 transition 안전망 역할 종료, Phase 4 에서 옵션 B 로 전환.

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

### Phase 4 — `ask_user` 도구 retire (옵션 B, 메인 채팅 한정)
**Done-when**: 메인 채팅 ask_user 도구 retire 완료, builder_v3 native interrupt 패턴 + 어댑터/UI/번역 모두 보존, 메인 채팅 회귀 0, 신규 가드 ≥2건 PASS

**LLM prompt 영향 분석 결과** (Phase 4 진입 시 확인):
- `executor.py:530-548` 의 system_prompt 동적 추가 영역(스킬 규칙 + skills_block) 에 `ask_user` 직접 언급 없음.
- 시드 템플릿/모델 system_prompt 에도 `ask_user` 텍스트 없음.
- 단, `tools/ask_user.py:13-28` docstring 이 OpenAI tools 배열에 description 으로 노출되어 implicit prompt 로 작용. retire 시 도구 카드 자체 미주입 → LLM 호출 자체 불가 → prompt 회귀 0.

**Backend 작업**:
- `backend/app/agent_runtime/tools/ask_user.py` 삭제
- `executor.py`:
  - L34 `from .tools.ask_user import ask_user as ask_user_tool` import 제거
  - L439-443 `include_ask_user: bool = True` 파라미터 제거 (모든 호출처)
  - L502-504 `if not include_ask_user: interrupt_on = None` (트리거 차단 우회) 제거
  - L506-509 `interrupt_on.setdefault("ask_user", {"allowed_decisions": ["respond"]})` 자동 등록 제거
  - L555-558 `if include_ask_user: langchain_tools.append(ask_user_tool)` conditional append 제거
- `streaming.py:84-117` ask_user 어댑터 분기는 **코드 보존 + 주석 갱신** ("builder_v3 native interrupt → 표준 respond action chunk 어댑터"로 의도 명시)

**Frontend 작업** (코드 변경 없음, 주석만):
- `lib/chat/tool-ui-registry.ts:54` 주석 갱신 — "Builder v3 전용 (메인 채팅 ask_user 도구 retired)"
- `lib/chat/use-chat-runtime.ts:100, 430-432` 주석 갱신 — "builder_v3 native interrupt 어댑터 경유"
- `components/chat/tool-ui/user-input-ui.tsx` JSDoc — "Builder v3 전용" 명시
- `messages/ko.json:558-571` `userInput` 라벨 보존 (builder_v3 사용)

**Tests**:
- `tests/test_executor.py:125-129, 185, 235, 272, 344, 551-575` — ask_user 자동 주입 어설션 갱신/삭제
- `tests/test_hitl_middleware.py:117` — `include_ask_user=False` 호출 제거(파라미터 자체 삭제)
- `tests/test_hitl_wire.py:280-295, 337-350` — 시나리오를 "builder_v3 native interrupt → 표준 respond chunk" 로 명확화 (이름/주석)
- `tests/test_builder_v3.py:177-230` 보존
- **신규 가드 ≥2건** (M5):
  1. 메인 채팅 도구 미주입 가드 — `include_ask_user` 파라미터 부재 + `langchain_tools` 에 `ask_user` 없음 검증
  2. builder_v3 native interrupt → streaming 어댑터 → 표준 respond chunk 파이프라인 회귀 가드

### Phase 5 — Builder v3 wire format 통일 (옵션, ~150 라인)
**Done-when**: Builder v3 의 ResumeRequest 도 표준 `decisions` 형식 받음 (graph 자체는 변경 X)

- builder routes 의 `BuilderResumeRequest` 도 dual-shape → 표준만
- frontend builder UI 동일 어댑터 사용
- builder graph 자체는 native interrupt 패턴 유지

---

## 위험 + 완화

| 위험 | 완화 |
|------|------|
| Wire format 변경 = breaking change | dual-path transition window — 한 PR 에서 둘 다 받기, 4 PR 후 제거 |
| Multi-action UI 신규 디자인 부재 | `ApprovalCard` 가 이미 단일 액션 지원 — 배열 렌더링 + 일괄 확정 버튼 추가만 |
| `ask_user` LLM prompt 변경 시 회귀 | Phase 4 에서 retire — 도구 미주입으로 LLM 호출 자체 불가, system_prompt 직접 언급 없음(분석 완료), prompt 회귀 0 |
| `ask_user` 도구가 builder_v3 의존성과 얽힘 | 메인 채팅 도구만 retire, builder_v3 native interrupt 패턴 + streaming 어댑터 + UI 컴포넌트 + ko.json 라벨 모두 보존. M5 가드 테스트로 builder_v3 파이프라인 회귀 0 확인 |
| Builder v3 graph 영향 | wire format 만 통일, graph 자체 변경 X — Phase 5 가 분리됨 |
| 트리거 모드에서 interrupt 발생 시 hang | Phase 4 retire 후 `include_ask_user` indicator 자체가 사라지므로 우회 코드 제거. 표준 미들웨어가 트리거 경로에서 자동 승인 처리 |
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
- Phase 4: 메인 채팅 ask_user 도구 미주입 가드 + builder_v3 native interrupt → streaming 어댑터 → 표준 respond chunk 파이프라인 회귀 0
- Phase 5: builder graph state 회귀 0

수동 e2e:
- 일반 도구 (e.g. write_file) 호출 → approve/reject/edit 각 액션 동작
- multi tool_call (e.g. delete_file + send_notification) 동시 emit → 배열 검토 → 일괄 결정
- ask_user 호출 → respond 동작
- 트리거에서 interrupt-가능 도구 호출 → 자동 승인

---

## 결정 근거 요약 (TL;DR)

deep agents 도입 후 자체 HiTL 구현이 표준 미들웨어의 가치 (도구별 정책 / SubAgent 상속 / 트리거 자동 승인 / multi tool_call 일괄) 를 막고 있다. 메인 채팅을 표준 `HumanInTheLoopMiddleware` 로 마이그레이션하면 (a) 트리거 모드에서 HiTL 정책 의미 있게 적용 가능, (b) 도구별 위험도 차등 (PRD 의 핵심 시나리오), (c) 한 AIMessage 의 multi tool_call 사용자 클릭 1번. Builder v3 는 deterministic state machine 패턴이라 직교 관계, 자체 유지가 맞음. 비용은 5 Phase, 약 800 라인, 회귀 위험은 dual-path transition 으로 완화.
