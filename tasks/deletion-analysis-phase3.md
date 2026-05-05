# Deletion Analysis — HiTL Phase 3 Transition End

> 베조스(read-only) 분석 + 사티아 검증 (실제 라인은 구현 단계에서 재확인).
> 브랜치: `feature/hitl-phase3-transition-end` (main `be5a735`).
> 목표: legacy wire format 완전 제거 (~80줄 변경, 사용자 무영향 clean break).

---

## A. Backend Legacy

### A1. `backend/app/schemas/conversation.py`
- 제거: `ResumeRequest.response` 필드 + `@deprecated` 주석
- 제거: dual-shape `_at_least_one` `model_validator` (둘 중 하나 이상 검증)
- 보존: `Decision` 모델 (4 type: approve/edit/reject/respond), `decisions` 필드
- 제거 후 `ResumeRequest`: `decisions: list[Decision]` 필수 단일 필드

### A2. `backend/app/routers/conversations.py:resume_message`
- 제거: `_legacy_response_to_decisions()` 헬퍼 (legacy → respond decision 변환)
- 제거: `if data.decisions is not None: ... else: legacy 변환 ...` dual 분기
- 제거: transition 로깅
- 제거 후: 표준 단일 경로 `resume_payload = {"decisions": [...]}`

### A3. `backend/app/agent_runtime/streaming.py`
- 제거: 정상 분기의 legacy chunk emit (`{interrupt_id, value}`)
- **변경**: fallback 경로 (`aget_state` 실패) — legacy chunk → empty standard chunk (`{interrupt_id: "", action_requests: [], review_configs: []}`)
- 보존: 표준 chunk emit (`{interrupt_id, action_requests, review_configs}`)

### A4. `backend/tests/test_hitl_phase2_wire.py` → rename `test_hitl_wire.py`
- 보존: 표준 시나리오 (Decision/decisions/표준 streaming)
- 제거: `response` 필드 시나리오 (str/list/dict)
- 제거: dual-shape 공존 시나리오 (`both decisions and response`)
- 제거: 422 dual 누락 검증 (단일 필드는 422 재정의)
- **변경**: fallback 테스트 — legacy emit 검증 → empty standard emit 검증
- expected-fail 테스트 (fallback 2회 호출 회귀) → 자연 해소 (legacy emit 제거되므로)

---

## B. Frontend Legacy

### B1. `frontend/src/lib/types/index.ts`
- 제거: `LegacyInterruptPayload` 인터페이스
- 변경: `InterruptPayload = StandardInterruptPayload` (union → 단일 alias 또는 직접 사용)
- 보존: `StandardInterruptPayload`, `Decision`, `ResumeDecisionsRequest`

### B2. `frontend/src/lib/sse/stream-resume.ts`
- 제거: `streamResume()` 함수 + `ResumeRequest` 타입 (legacy)
- 보존: `streamResumeDecisions()` + `ResumeDecisionsRequest`

### B3. `frontend/src/lib/chat/hitl-context.ts`
- 제거: `onResume(response, displayText)` 콜백 (legacy 어댑터)
- 보존: `onResumeDecisions(decisions, displayText)` 콜백

### B4. `frontend/src/lib/chat/use-chat-runtime.ts`
- 제거: `case 'interrupt'` 의 legacy 분기 (`'value' in data`)
- 제거: `handledStandardInterruptIdsRef` (dedup ref) — 단일 경로면 불필요
- 제거: `onResume` 콜백 + `streamResume` import
- 보존: 표준 처리 (`onStandardInterrupt`)

### B5. `frontend/messages/ko.json`
- 검증 후 사용 중인 라벨만 유지 (대부분 표준 공용으로 보존)

### B6. **Tool UI 호출 사이트 마이그레이션** ⚠️ Day 1 위험
다음 컴포넌트는 `onResume` 호출 → `onResumeDecisions`로 변경:
- `frontend/src/components/.../approval-card.tsx`
- `frontend/src/components/.../image-generation-ui.tsx`
- `frontend/src/components/.../user-input-ui.tsx`
- `frontend/src/lib/.../use-approval-form.ts` (훅)
- chat 페이지 (HiTLContext provider 부분)

베조스 분석 라인은 추정 — **구현 단계에서 grep으로 재확인 필수**.

### B7. Frontend 테스트
- `stream-resume.test.ts` (6건): legacy 2건 제거, 표준 4건 보존
- `use-chat-runtime-hitl.test.tsx` (9건): legacy/fallback 시나리오 정리, 표준 보존

---

## C. 보존 영역 (수정 금지)
- `backend/app/agent_runtime/middleware_registry.py` (Phase 1)
- `backend/app/agent_runtime/executor.py:_prepare_agent` (Phase 1, 트리거 차단)
- `backend/tests/test_hitl_middleware.py` (Phase 1, 5건 회귀 가드)
- `backend/app/agent_runtime/tools/ask_user.py` (Phase 4까지)
- `backend/app/agent_runtime/builder_v3/**` (Phase 5까지, 자체 native interrupt)

---

## D. 최종 검증 grep (Phase 3 완료 후 0건)

```bash
# Backend
rg -n "ResumeRequest.*response[^_]|_legacy_response_to_decisions" backend/app

# Frontend (단어 경계로 *Decisions 변형 제외)
rg -nw "streamResume" frontend/src
rg -nw "onResume" frontend/src
rg -n "handledStandardInterruptIdsRef" frontend/src
rg -n "LegacyInterruptPayload" frontend/src
```

---

## E. 위험 분석 (Day 1)

| 위험 | 위험도 | 대응 |
|-----|-------|-----|
| Tool UI 호출 사이트 마이그레이션 누락 | **HIGH** | grep 전수조사 + 단위 테스트 갱신 |
| fallback empty array 처리 — frontend `'action_requests' in data` 검사 | LOW | empty array도 truthy 표준 |
| `handledStandardInterruptIdsRef` 제거 회귀 | LOW | 단일 경로 → dedup 불필요 |
| 페이지 useMemo HiTLContext 시그니처 변경 | MEDIUM | onResume 제거 → 페이지에서 미사용 확인 |

---

## F. 라인 카운트 추정

| 영역 | 줄 수 | 비고 |
|-----|------|-----|
| `conversation.py` (schema) | ~10 | 필드 + validator |
| `conversations.py` (router) | ~25 | 헬퍼 함수 + dual 분기 |
| `streaming.py` | ~15 | legacy emit + fallback 변경 |
| `types/index.ts` | ~5 | LegacyInterruptPayload |
| `stream-resume.ts` | ~15 | streamResume 함수 |
| `hitl-context.ts` | ~5 | onResume |
| `use-chat-runtime.ts` | ~25 | onResume + dedup + legacy case |
| Tool UI | 호출 사이트 변경 | 5-10건 |
| 테스트 | -10건 정리 | rename + 시나리오 정리 |
| **합계** | **~100줄 변경** | HANDOFF "~80라인" 추정과 ±20 일치 |
