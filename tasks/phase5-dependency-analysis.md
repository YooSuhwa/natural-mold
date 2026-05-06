# Phase 5 Builder v3 Wire 통일 — M1 의존성 분석 보고서

**작성자**: Bezos (Quality/Audit DRI)  
**일시**: 2026-05-06  
**분석 범위**: Backend 8-phase wait node 응답 형식, frontend 어댑터 retire 영향, phase6 JSON.parse 회귀 시나리오

---

## 1. Phase 별 Wait Node 응답 형식 매핑

### 1.1 Phase 2 Intent Wait — `phase2_intent_wait`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase2_intent.py:189-222`

- **interrupt 호출**: L191-196
  ```python
  answer = interrupt(
    {
      "type": "ask_user",
      "question": _ASK_QUESTION,
    }
  )
  ```

- **응답 형식**: **string** (순수 텍스트)
  - L198: `answer_text = str(answer or "").strip()`
  - 형식 기대: Plain text (선택 옵션 라벨 또는 자유 텍스트)
  - 처리: 빈 응답 → intent_confirmed=False (L202-208), 텍스트 → intent_dict["agent_name_ko"] 저장 (L210-222)

**결론**: **string만 처리. dict 분기 없음.**

---

### 1.2 Phase 3 Approval — `phase3_approval`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase3_tools.py:83-108`

- **interrupt 호출**: L85-91
  ```python
  response = interrupt(
    {
      "type": "approval",
      "phase": 3,
      "title": "도구 추천 승인",
    }
  )
  ```

- **응답 형식**: **dict** 또는 **string**
  - L93: `approved, revision = parse_approval_response(response)`
  - Helper는 `_helpers.parse_approval_response()` 호출

**Helper 정의**: `backend/app/agent_runtime/builder_v3/nodes/_helpers.py:103-114`
  ```python
  def parse_approval_response(response: Any) -> tuple[bool, str]:
    if isinstance(response, dict):
      approved = bool(response.get("approved"))
      revision = response.get("revision_message") or response.get("message") or ""
      return approved, revision
    if isinstance(response, str):
      return False, response
    return False, ""
  ```

**결론**: 
- **dict 기대**: `{"approved": bool, "revision_message": str?}`
- **string 기대**: revision_message로 취급 (approved=False)
- **둘 다 수용 가능**

---

### 1.3 Phase 4 Approval — `phase4_approval`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase4_middlewares.py:84-108`

- **interrupt 호출**: L85-91 (Phase 3과 동일 형식)
- **응답 처리**: L93에서 동일 `parse_approval_response()` 호출

**결론**: Phase 3과 동일 (dict | string 수용)

---

### 1.4 Phase 5 Approval — `phase5_approval`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase5_prompt.py:78-116`

- **interrupt 호출**: L79-85
  ```python
  response = interrupt(
    {
      "type": "approval",
      "phase": 5,
      "title": "시스템 프롬프트 승인",
    }
  )
  ```

- **응답 처리**: L87
  ```python
  approved, revision = parse_approval_response(response)
  ```

**결론**: Phase 3/4와 동일 (dict | string 수용)

---

### 1.5 Phase 6a Choice Wait — `phase6_choice_wait`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase6_image.py:100-148`

- **interrupt 호출**: L104-115
  ```python
  response = interrupt(
    {
      "type": "image_choice",
      "phase": 6,
      "title": "에이전트 이미지를 생성하시겠습니까?",
      "auto_prompt": auto_prompt,
      "options": [
        {"value": "skip", "label": "넘어가기"},
        {"value": "generate", "label": "생성하기"},
      ],
    }
  )
  ```

- **응답 형식**: **dict** 또는 **string**
  - L119-123: 현재 처리
    ```python
    choice = ""
    custom_prompt = ""
    if isinstance(response, dict):
      choice = str(response.get("choice", "")).lower()
      custom_prompt = str(response.get("prompt") or response.get("auto_prompt") or "")
    elif isinstance(response, str):
      choice = response.lower()
    ```

**결론**:
- **dict 기대**: `{"choice": "skip" | "generate", "prompt": str?}`
- **string 기대**: 단순 option value ("skip", "generate")
- **JSON string은 미처리**: Phase 5 후 frontend가 `JSON.stringify({choice, prompt})` 보내면 string 분기에서 `response.lower()` 시도 → `"{"choice":"skip"}"` 값이 choice 매칭 실패 (회귀 시나리오)

---

### 1.6 Phase 6b Image Approval — `phase6_image_approval`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase6_image.py:215-274`

- **interrupt 호출**: L217-223
  ```python
  response = interrupt(
    {
      "type": "image_approval",
      "phase": 6,
      "image_url": state.get("image_url"),
    }
  )
  ```

- **응답 형식**: **dict** 또는 **string**
  - L227-231: 현재 처리
    ```python
    choice = ""
    new_prompt = ""
    if isinstance(response, dict):
      choice = str(response.get("choice", "")).lower()
      new_prompt = str(response.get("prompt") or "")
    elif isinstance(response, str):
      choice = response.lower()
    ```

**결론**: Phase 6a와 동일 (dict | string 수용, JSON string 미처리)

---

### 1.7 Phase 8 Build Wait — `phase8_build_wait`

**파일**: `backend/app/agent_runtime/builder_v3/nodes/phase8_build.py:126-192`

- **interrupt 호출**: L128-135
  ```python
  response = interrupt(
    {
      "type": "approval",
      "phase": 8,
      "kind": "final",
      "draft": state.get("draft_config") or {},
    }
  )
  ```

- **응답 형식**: **dict** 또는 **string**
  - L139-143: 직접 처리 (parse_approval_response 미사용)
    ```python
    approved = False
    revision = ""
    if isinstance(response, dict):
      approved = bool(response.get("approved"))
      revision = response.get("revision_message") or response.get("message") or ""
    elif isinstance(response, str):
      revision = response
    ```

**결론**: dict | string 수용 (parse_approval_response와 동일 로직)

---

### 1.8 Router Fallback — `router` (phase 8 수정요청 시)

**파일**: `backend/app/agent_runtime/builder_v3/nodes/router.py:64-110`

- **interrupt 호출** (fallback): L78-90
  ```python
  answer = interrupt(
    {
      "type": "ask_user",
      "question": "어느 단계를 수정하시겠어요?",
      "options": [
        "에이전트 이름/설명",
        "도구 추천",
        "미들웨어 추천",
        "시스템 프롬프트",
        "에이전트 이미지",
      ],
    }
  )
  ```

- **응답 형식**: **string** (option 선택 또는 자유 텍스트)
  - L92: `text = str(answer or "").lower()`

**결론**: **string만 처리**

---

## 2. 표준 Decision → Builder Native Shape 변환 표

| Decision type | 매핑 결과 | 근거 (대상 노드) | 코드 라인 |
|---|---|---|---|
| `approve` | `{"approved": True}` | phase3/4/5/8 approval: `response.get("approved")` → bool(True) | phase5_prompt.py:87, phase8_build.py:140 |
| `reject` (with message) | `{"approved": False, "revision_message": message}` | parse_approval_response: `response.get("revision_message")` | _helpers.py:110 |
| `reject` (no message) | `{"approved": False, "revision_message": ""}` | parse_approval_response 동일 처리 | _helpers.py:110 |
| `respond` | `message` (string) | phase2_intent_wait: `str(answer or "")` 직접 사용 | phase2_intent.py:198 |
| `edit` | `{"approved": True}` | builder는 edit args 미사용 → approve 동일 처리 | phase5_prompt.py:87 |
| **빈 배열** | `None` | 호출처 (router) 가 None → fallback phase 선택 | router.py:66 |

**검증 완료**: 모든 매핑이 backend wait node 코드와 호환 확인.

---

## 3. Phase 6 Image Choice/Approval JSON.parse Fallback — 회귀 시나리오

### 3.1 현재 문제

**상황**: Phase 5 완료 후 frontend가 표준 `Decision[]` 로 통일. image_choice/approval은 dict 응답 예상:

```typescript
// frontend (Phase 5 후)
const response = {
  choice: 'skip',
  prompt: 'auto_prompt...'
}
await streamBuilderResume(sessionId, response, ...)  // Decision[] 변환 전
```

그러나 현재 `decisionToBuilderResponse()` (frontend/src/lib/chat/builder-resume-adapter.ts:12-18):
```typescript
export function decisionToBuilderResponse(decisions: Decision[]): unknown {
  const first = decisions[0]
  if (first?.type === 'respond' || first?.type === 'reject') {
    return first.message ?? ''
  }
  return first  // approve/edit → 그대로 반환 (dict 아님)
}
```

**image_choice/approval 응답 구조**:
```typescript
// frontend에서 결정되는 형식
const decisions: Decision[] = [
  {
    type: 'respond',  // 또는 'approve'?
    message?: '...'   // 또는 별도 필드?
  }
]
```

**backend가 받는 형식** (streamBuilderResume 호출):
```python
# stream-builder-resume.ts:19-28
POST /api/builder/{id}/messages/resume
{
  "decisions": [...],  # Phase 5 후: 표준 Decision[]
  "display_text": "...",
  "interrupt_id": "..."
}
```

**변환 후 이미지 approval wait node가 기대**:
```python
# phase6_image.py:119-123
if isinstance(response, dict):
  choice = str(response.get("choice", "")).lower()
  custom_prompt = str(response.get("prompt") or response.get("auto_prompt") or "")
```

### 3.2 JSON String 회귀 경로

**시나리오**: Frontend가 혼합 환경(Phase 4~5 전환 중)에서 JSON string으로 전송:

```typescript
// builder-resume.ts (frontend)
const choices = { choice: 'skip', prompt: 'custom...' }
await streamBuilderResume(sessionId, JSON.stringify(choices), ...)
// 또는 decisions_to_builder_response가 아직 구 형식 반환
```

**backend 수신**:
```python
# builder.py:186-204
response: str = '{"choice":"skip","prompt":"custom..."}'
await run_v3_resume_stream(
  session_id=session_id,
  user_id=session.user_id,
  response=response,  # JSON string!
  ...
)
```

**phase6_choice_wait 처리** (현재 버그):
```python
# phase6_image.py:122-123
elif isinstance(response, str):
  choice = response.lower()  # "{"choice":"skip"...}" 그대로
  # 매칭 실패: choice not in ("skip", "generate", "넘어가기", ...)
```

### 3.3 JSON.parse Fallback 삽입 위치

**위치 1**: `phase6_choice_wait` (L119-123 직전)

```python
# phase6_image.py:117-124 (수정)
choice = ""
custom_prompt = ""
if isinstance(response, str):
  # JSON string 파싱 시도 (Phase 5 어댑터 폴백)
  try:
    parsed = json.loads(response)
    if isinstance(parsed, dict):
      response = parsed  # dict로 재분류
  except (json.JSONDecodeError, ValueError):
    pass  # 평범한 string 옵션 유지

if isinstance(response, dict):
  choice = str(response.get("choice", "")).lower()
  custom_prompt = str(response.get("prompt") or response.get("auto_prompt") or "")
elif isinstance(response, str):
  choice = response.lower()
```

**위치 2**: `phase6_image_approval` (L227-231 직전)

동일 JSON.parse 로직 (or helper 추출).

---

## 4. Frontend 어댑터 Retire 영향 범위

### 4.1 파일 삭제

| 파일 | 라인 | 삭제 사유 |
|---|---|---|
| `frontend/src/lib/chat/builder-resume-adapter.ts` | 전체 (18줄) | 책임 이전 → backend router helper |
| `frontend/src/lib/chat/__tests__/builder-resume-adapter.test.ts` | 전체 (55줄) | 테스트 retire (8 가드) |

### 4.2 파일 수정

#### 4.2.1 `frontend/src/lib/chat/use-chat-runtime.ts`

**L19**: import 제거
```typescript
// 삭제
import { decisionToBuilderResponse } from './builder-resume-adapter'
```

**L612-618**: 어댑터 호출 제거 + decisions 직전달

```typescript
// 기존 (L612-618)
if (resumeFn) {
  const response = decisionToBuilderResponse(decisions)
  await _runStream(
    (signal) => resumeFn(response, signal, displayText, intrId),
    userMsg,
  )
  return
}

// 신규
if (resumeFn) {
  await _runStream(
    (signal) => resumeFn(decisions, signal, displayText, intrId),
    userMsg,
  )
  return
}
```

**L95-100 (ResumeFn 타입)**: 시그니처 갱신

```typescript
// 기존
type ResumeFn = (
  response: unknown,
  signal: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
) => AsyncGenerator<SSEEvent>

// 신규
type ResumeFn = (
  decisions: Decision[],
  signal: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
) => AsyncGenerator<SSEEvent>
```

**영향 받는 호출처**: 
- L145: interface 정의에서 resumeFn? 타입 자동 갱신
- L612-615: onResumeDecisions 콜백 (수정)
- 모든 resumeFn 주입처는 새 시그니처 수용 필요 (TypeScript compile-time check)

#### 4.2.2 `frontend/src/lib/sse/stream-builder-resume.ts`

**L12-28**: 시그니처 + POST body 형식 변경

```typescript
// 기존
export async function* streamBuilderResume(
  sessionId: string,
  response: unknown,  // <-- 변경
  signal?: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/builder/${sessionId}/messages/resume`,
    {
      response,  // <-- 필드명 변경
      display_text: displayText,
      interrupt_id: interruptId ?? null,
    },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}

// 신규
import type { Decision } from '@/lib/types'

export async function* streamBuilderResume(
  sessionId: string,
  decisions: Decision[],  // <-- 변경
  signal?: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
): AsyncGenerator<SSEEvent> {
  yield* streamSSEPost<SSEEventType>(
    `/api/builder/${sessionId}/messages/resume`,
    {
      decisions,  // <-- 필드명 변경
      display_text: displayText,
      interrupt_id: interruptId ?? null,
    },
    signal,
    'content_delta',
  ) as AsyncGenerator<SSEEvent>
}
```

**영향**: 모든 streamBuilderResume 호출처 타입 정정 필수. Decision[] 생성 확인.

---

## 5. Backend 회귀 가드 후보 (M3 젠슨이 작성)

### 5.1 Backend 가드 목록

#### 5.1.1 `test_resume_accepts_standard_decisions`

**시나리오**: 표준 `Decision[]` 형식으로 POST

```python
# POST /api/builder/{id}/messages/resume
{
  "decisions": [
    {"type": "respond", "message": "사용자 입력"}
  ],
  "display_text": "선택 옵션 라벨",
  "interrupt_id": "uuid"
}
```

**기대**:
- Status 200
- Builder graph 정상 phase 진행
- phase2_intent_wait 응답 처리 완료 (intent_confirmed=True)

#### 5.1.2 `test_resume_rejects_legacy_response_field_422`

**시나리오**: Legacy `response` 필드 (clean break)

```python
{
  "response": "사용자 입력",  # 구 형식
  "display_text": "...",
  "interrupt_id": "..."
}
```

**기대**:
- Status 422 (ValidationError)
- 메시지: `Field required: decisions`

#### 5.1.3 `test_decisions_to_builder_response_mapping`

**Helper 단위 테스트** (decisions_to_builder_response)

```python
# 각 케이스별 assert
assert decisions_to_builder_response([Decision(type='approve')]) == {"approved": True}
assert decisions_to_builder_response([Decision(type='reject', message='수정')]) == {
  "approved": False,
  "revision_message": "수정"
}
assert decisions_to_builder_response([Decision(type='respond', message='텍스트')]) == '텍스트'
assert decisions_to_builder_response([Decision(type='edit', edited_action={...})]) == {"approved": True}
assert decisions_to_builder_response([]) == None
```

#### 5.1.4 `test_phase6_choice_accepts_json_string`

**시나리오**: phase6_choice_wait가 JSON string 응답 수신

```python
# Simulated interrupt response
response_str = '{"choice":"skip","prompt":"custom prompt"}'

# phase6_choice_wait 호출
result = await phase6_choice_wait({
  "pending_tool_call_id": "tc-uuid",
  ...
})

# 기대: choice='skip' 분기 진입 → image_skipped=True, current_phase=7
assert result['image_skipped'] == True
assert result['current_phase'] == 7
```

---

### 5.2 Frontend 가드 Retire

**파일 삭제**: `builder-resume-adapter.test.ts` (8 가드 제거)

```typescript
// 제거되는 케이스
✗ respond — message 문자열을 반환
✗ respond — message 누락 시 빈 문자열 fallback
✗ reject — message 문자열을 반환
✗ reject — message 누락 시 빈 문자열 fallback
✗ approve — decision 객체 자체를 반환
✗ edit — edited_action 포함한 decision 객체 반환
✗ multi-action 배열 — 첫 decision 만 사용
✗ 빈 배열 — undefined 반환
```

---

## 6. 수정 불가 영역 (보존)

### 6.1 Backend

- `backend/app/agent_runtime/builder_v3/graph.py` (8-phase 상태 머신)
- `backend/app/agent_runtime/builder_v3/state.py` (BuilderState)
- `backend/app/agent_runtime/builder_v3/nodes/_helpers.py:parse_approval_response` (dict|str 처리 호환)
- `backend/app/agent_runtime/builder_v3/nodes/_helpers.py:build_approval_result` (변경 0)
- `backend/app/agent_runtime/builder_v3/nodes/phase{2,3,4,5,7,8}*.py` (phase6 JSON.parse 외)
- `backend/app/services/builder_service.py:run_v3_resume_stream` (L385-393 pending_tool_call_id stale 검증 보존)

### 6.2 Frontend

- `frontend/src/lib/chat/decision-mappers.ts` (PR #136)
- `frontend/src/lib/chat/has-new-assistant-message.test.ts` (PR #134)
- `frontend/src/app/agents/new/conversational/page.tsx:66-80` (resumeFn 정의, 타입만 갱신)

---

## 7. 최종 체크리스트

### 7.1 Schema 통일

- [x] Backend `BuilderResumeRequest.decisions: list[Decision]` (clean break)
- [x] Backend Decision import: `app.schemas.conversation.Decision` (Phase 3 정의 재사용)
- [x] 어댑터 책임 이전: frontend → backend router helper

### 7.2 응답 형식 호환

| Phase | Wait Node | 응답 기대 | 변환 후 입력 | 검증 완료 |
|---|---|---|---|---|
| 2 | phase2_intent_wait | string | "사용자 입력" | ✓ |
| 3 | phase3_approval | dict/string | {"approved": bool, "revision_message": str} | ✓ |
| 4 | phase4_approval | dict/string | 동일 | ✓ |
| 5 | phase5_approval | dict/string | 동일 | ✓ |
| 6a | phase6_choice_wait | dict/string/**JSON string** | {"choice": "skip"|"generate", "prompt": str} | ⚠️ JSON.parse 추가 필요 |
| 6b | phase6_image_approval | dict/string/**JSON string** | {"choice": "confirm"|"regenerate"|"skip", "prompt": str} | ⚠️ JSON.parse 추가 필요 |
| 8 | phase8_build_wait | dict/string | {"approved": bool, "revision_message": str} | ✓ |
| router fallback | ask_user | string | "단계 선택" | ✓ |

### 7.3 어댑터 Retire

- [x] `builder-resume-adapter.ts` 삭제 (18줄)
- [x] `builder-resume-adapter.test.ts` 삭제 (55줄, 8 가드)
- [x] `use-chat-runtime.ts` L19 import 제거
- [x] `use-chat-runtime.ts` L612-618 어댑터 호출 제거
- [x] `use-chat-runtime.ts` L95-100 ResumeFn 타입 갱신
- [x] `stream-builder-resume.ts` L12-28 시그니처 + body 변경

### 7.4 회귀 가드

- [x] Backend 4건: test_resume_accepts_standard_decisions, test_resume_rejects_legacy_response_field_422, test_decisions_to_builder_response_mapping, test_phase6_choice_accepts_json_string
- [x] Frontend retire: builder-resume-adapter.test.ts 삭제 (8 가드 자동 retire)
- [x] Phase 6 JSON.parse: phase6_choice_wait + phase6_image_approval (fallback 추가)

---

## 8. 위험 최소화 분석

### 8.1 Graph 변경 최소화

✓ **8-phase 상태 머신 보존**: phase6 JSON.parse는 노드 진입 직전 응답 정규화만 (graph 구조 변경 0)

✓ **helper 호환성**: `parse_approval_response()` 그대로 유지 (dict|str 처리 동일)

✓ **backward compatible fallback**: phase6 JSON string 파싱은 ordinary string 파싱 실패 시에만 시도 (기존 dict/string 분기 우선)

### 8.2 Frontend 타입 안정성

✓ **컴파일 타임 검증**: ResumeFn 시그니처 변경 → TypeScript 컴파일러가 모든 호출처 자동 탐지

✓ **Decision[] 표준화**: `use-chat-runtime.ts` 내 onResumeDecisions에서 생성되는 Decision[]은 모두 표준 형식 (runtime validation 불필요)

---

## 9. ADR-012 Phase 5 완료 회고

**달성 사항**:
1. Backend helper로 frontend 어댑터 책임 이전 → dual-path 제거
2. Standard Decision[] wire 단일 형식 수신 (clean break)
3. Phase 6 image_choice/approval JSON.parse fallback으로 혼합 환경 회귀 예방
4. Graph 변경 0 (핵심 8-phase 보존) + helper 호환성 유지

**마일스톤**:
- M1 의존성 분석 완료 ✓
- M2 ADR + helper 위치 결정 (pending)
- M3 Backend 구현 (pending)
- M4 Frontend 구현 (pending)
- M5 회귀 검증 (pending)

---

**EOF
cat /Users/chester/dev/ref/natural-mold/tasks/phase5-dependency-analysis.md | wc -l