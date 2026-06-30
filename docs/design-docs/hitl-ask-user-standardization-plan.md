# HITL Hardening Implementation Plan — robust `edit` + `allowed_decisions` gating

> **For agentic workers:** 이 문서 하나만 보고 처음부터 끝까지 구현할 수 있도록 작성했다. 모든 변경은 파일 경로 + 함수명 + 코드 스니펫 + 테스트 + 검증 커맨드를 포함한다. 단계는 체크박스(`- [ ]`)로 추적한다. 먼저 §2(현재 상태 — 이미 구현된 것)를 읽고, 절대 다시 만들지 말 것.

**작성 기준:** 현재 소스코드(`/Users/chester/dev/ref/natural-mold-captures`, langchain 1.3.9, deepagents 0.6.9) 직접 대조. 이전 버전 문서(executor.py 빌드 사이트, 수동 `HumanInTheLoopMiddleware` append)는 **전면 폐기**한다.

**Goal:** 메인 채팅 HITL(human-in-the-loop)에서 **`edit`(수정 후 승인) 결정을 견고화**하고, **도구별 `allowed_decisions` 화이트리스트를 실제로 존중**하도록 만든다. 부차적으로 이미 구현된 multi-action wire / subagent 상속의 *남은 갭*과 ask_user 통합 결정을 정리한다.

**Tech Stack:** FastAPI, LangChain 1.3.x (`HumanInTheLoopMiddleware`), LangGraph 1.x, DeepAgents 0.6.9 (`create_deep_agent(interrupt_on=...)`), React 19, assistant-ui, TanStack Query, Vitest, pytest.

---

## 1. 배경 — HITL 결정 4종

도구 실행 전 사용자 개입은 LangChain `HumanInTheLoopMiddleware`의 표준 interrupt 체계를 쓴다. 결정 타입은 4종:

| type | 의미 | 추가 필드 |
|---|---|---|
| `approve` | 그대로 실행 | 없음 |
| `edit` | **인자를 수정해서 실행** | `edited_action: {name, args}` |
| `reject` | 실행 거부 | `message?` (사유) |
| `respond` | 도구 실행 없이 사용자 답변을 모델에 반환 (ask_user 전용) | `message` |

도구별로 어떤 결정을 허용할지는 interrupt payload의 `review_configs[i].allowed_decisions`로 내려온다. 본 작업의 핵심은 **edit가 안정적으로 동작**하고 **카드가 allowed_decisions를 존중**하게 만드는 것이다.

---

## 2. 현재 상태 — 이미 구현된 것 (다시 만들지 말 것)

이전 문서가 "구현해야 한다"고 적었던 항목 중 대부분은 **이미 구현·테스트 완료**다. 재작업 금지.

### 2.1 ✅ Backend: top-level `interrupt_on` 경로 (subagent 상속 포함) — DONE

- **빌드 사이트가 이동했다.** `backend/app/agent_runtime/executor.py`는 이제 **re-export 파사드일 뿐**(파일 전체가 import 재노출). 실제 빌드는 `backend/app/agent_runtime/runtime_component_builder.py`.
- `build_agent()` (`runtime_component_builder.py:83-97`)는 `create_deep_agent(..., interrupt_on=interrupt_on, ...)`로 **top-level 파라미터**를 넘긴다. **app 코드에 수동 `HumanInTheLoopMiddleware(` 인스턴스는 0개**(grep 확인).
- 정책 계산: `_build_interrupt_on_policy()` (`runtime_component_builder.py:363-392`) = `_default_interrupt_on_from_tools()` (`:354-360`) + `middleware_configs`의 명시 `human_in_the_loop.params.interrupt_on` 병합 + `ask_user` 정책 `setdefault`.
- **도구별 정책은 `backend/app/tools/risk.py`가 결정한다** (이 작업의 allowed_decisions 출처):
  - `default_deepagents_interrupt_policy()` (`risk.py:271-276`): `write_file→[approve,reject]`, `edit_file→[approve,edit,reject]`, `execute→[approve,reject]`.
  - `interrupt_policy_for_tool()` (`risk.py:279-283`): 도구 risk 메타데이터로 **명시 allowed_decisions** 방출(`{tool: True}` 아님).
  - `_DEFAULT_APPROVAL_DECISIONS` (`risk.py:24-30`): WRITE_INTERNAL / EXTERNAL_MUTATION → `(approve, edit, reject)`; CODE_EXECUTION / UNKNOWN → `(approve, reject)`.
  - `execute_in_skill_risk()` (`risk.py:260-268`): CODE_EXECUTION → **`(approve, reject)` — edit 없음**.
  - MCP mutation (`risk.py:250-257`): `(approve, reject)`.
  - `ask_user`: `{"allowed_decisions": ["respond"]}` (`runtime_component_builder.py:390-391`).
- ask_user 순서 버그 FIXED: `ask_user_tool` / `execute_in_skill`은 정책 계산(`:690`) **전에** append(`:646`, `:688`).
- **Subagent 상속**(`subagents.py:74-168`): 각 child가 자기 도구로 자기 `interrupt_on`을 계산해 `spec["interrupt_on"]`에 기록(`:162-163`). deepagents 0.6.9 `graph.py:663`이 spec 값 우선, 없으면 top-level 상속; auto `general-purpose` subagent는 top-level 상속(`graph.py:741-746`).
- **Trigger 모드 차단**: `_build_interrupt_on_policy`가 None 반환(`:377-378`), ask_user 미주입(`:687-688`, `:737`), `trigger_executor.py:202-218`가 risky tool 자체를 사전 차단.
- `middleware_registry.py`: `human_in_the_loop` ∈ `EXPLICITLY_INSTANTIATED_TYPES`(`:459-464`) → `build_middleware_instances` 우회(`runtime_component_builder.py:609-611`), 카탈로그엔 노출 유지.
- 테스트: `tests/test_hitl_middleware.py`(top-level interrupt_on + `_hitl_instances == []` 가드), `tests/agent_runtime/test_subagents_runtime.py:184`, `tests/agent_runtime/test_langgraph_hitl_interrupts.py`.

### 2.2 ✅ Frontend: multi-action wire coordination — DONE

- `frontend/src/lib/chat/standard-interrupt.ts`: `standardInterruptToToolCalls()` (`:126-153`, action_request → 합성 tool call, 메타 `metadataForAction` `:45-58`로 `hitl_action_index/total/interrupt_id/approval_id/allowed_decisions` 부착), `createHiTLDecisionCoordinator()` (`:213-244`, **N개 결정을 모아 인덱스 순서대로 한 번만 resume**, idempotent).
- `reviewForAction()` (`:31-43`): review_config 없으면 fallback `allowed_decisions: ['approve','reject']`.
- v3 경로 어댑터 `frontend/src/lib/chat/langgraph-runtime/hitl-interrupts.ts`(`standardInterruptToToolCalls` 재사용 `:10`, 정규화/projection), 스트림 훅 wiring `use-moldy-langgraph-stream.ts:2328-2358`(projection), coordinator map `:2562`, `registerDecision` `:2871-2897`(단일 액션 bypass `:2880-2883`, 멀티 coordinator `:2884-2894`), 동시-인터럽트 배칭 `respondAll` `:2756-2811`.
- 카드 dispatch: `approval-card.tsx`의 `resumeDecision`이 `hitl_action_index` 있으면 `registerDecision` 우선, 없으면 `onResumeDecisions` fallback. `user-input-ui.tsx` 동일.
- `hitl-context.ts:12-17`: `registerDecision` API.
- 테스트: `standard-interrupt.test.ts`(coordinator 포함), `use-moldy-langgraph-stream.test.tsx`(멀티/동시 배칭), `langgraph-runtime/__tests__/hitl-interrupts.test.ts`.

### 2.3 ✅ ask_user wire 정규화 — DONE (단, native interrupt 기반)

- ask_user는 LLM-visible tool이며 정책에 `[respond]`로 등록되지만, 실제 대기는 **tool body 내부 native `interrupt()`** (`backend/app/agent_runtime/tools/ask_user.py:152`)로 발생. `streaming._interrupt_to_standard_chunk`(`streaming.py:198-221`)가 native 페이로드를 표준 `respond` action으로 어댑트. resume 파싱 `_extract_respond_message`(`ask_user.py:62-77`)가 `{"decisions":[{"type":"respond","message":...}]}`와 bare string 모두 처리.

### 2.4 ❌ 아직 없는 것 (= 본 작업 범위)

1. **`edit` 견고화** — 프론트가 `tool_name` 모르면 하드 중단, raw JSON 텍스트박스, 시크릿 위치 의존 복원(아래 §3).
2. **`allowed_decisions` 게이팅** — 카드가 값을 받지만 **버튼 표시에 안 씀**(잠재 버그). edit 불가 도구(`execute_in_skill` 등)에도 수정 버튼이 떠서, 누르면 미들웨어가 늦게 ValueError.
3. (선택) **통합 "N건 대기" UX** — wire는 됐지만 시각적 묶음/「모두 승인」 없음.
4. (선택/결정) **ask_user를 미들웨어 respond로 진짜 통합**할지 vs 현재 native+wire 정규화 유지·문서화할지.
5. (선택) **부모 커스텀 HITL 정책의 linked subagent 전파** — 현재 child는 자기 `middleware_configs`만 읽어(`subagents.py:118`) 부모 override가 전파 안 됨.

---

## 3. 문제 상세 — `edit`가 깨지는 지점 (현재 코드)

`frontend/src/components/chat/tool-ui/approval-card.tsx` 기준.

### 3.1 하드 중단 — `tool_name` 미상 시 edit 불가
`toDecision('modified', resumeResponse, args?.tool_name)`(`:~118-133`, 호출 `:~360`):
```ts
case 'modified':
  if (!toolName) return null            // ← 하드 중단
  return toEdit({ name: toolName, args: response.modified_args ?? {} })
```
`null`이면 핸들러가 submit 전체를 중단하고 **잘못된 메시지** `invalidJson`을 띄운다(`:~361-367`). `tool_name`은 `standard-interrupt.ts:146`에서 `action.name`으로 채워지지만, 병합 경로에서 raw 모델 tool-call로부터 온 슬롯은 `tool_name`이 비어 edit만 실패한다(approve/reject는 정상).

**근본 원인:** 프론트가 `edited_action.name`(도구 이름)을 **재구성해 보내야 한다**. langchain `human_in_the_loop.py:310-320`이 `edited_action["name"]`/`["args"]`를 hard subscript로 읽기 때문.

### 3.2 raw JSON 텍스트박스 — 문법 에러로 깨짐
edit 진입 시 `editedArgs`에 `JSON.stringify(toolArgs, null, 2)`를 채우고 textarea로 편집(`:~480-495`, `:~515-527`). submit 시 `JSON.parse(editedArgs)`(`:~342-358`) — 중괄호/따옴표 하나만 틀려도 `invalidJson`.

### 3.3 시크릿(`<redacted>`) 위치 의존 복원 — 누출/유실
`tool_args`는 **소스에서 이미 redact**됨(`standard-interrupt.ts:130` `redactSensitiveRecord`). 따라서 프론트 `restoreRedactedRecordPlaceholders(parsed, args?.tool_args)`(`:~56-80`, `:~347-350`)는 **프로덕션에서 no-op**(원본도 `<redacted>`라 되돌릴 값이 없음). 실제 복원은 백엔드가 checkpoint에서 한다. 더 나쁜 건: 프론트/백엔드 복원이 **key 이름·배열 index 위치 매칭**이라, 사용자가 `<redacted>` 키를 rename / 배열 reorder / 라인 삭제하면 시크릿이 **그대로 전송되거나 유실**된다(에러 없이).
> ⚠️ 기존 테스트 `approval-card.test.tsx`의 "restores redacted placeholders…"는 `tool_args`에 **un-redacted** 값을 직접 주입해 통과 — 프로덕션에서 동작하지 않는 경로에 잘못된 안도감을 준다. 본 작업에서 이 테스트를 교체한다.

### 3.4 `allowed_decisions` 잠재 버그
`ApprovalArgs.allowed_decisions`(`:40`)는 채워지지만(`standard-interrupt.ts:150`) **렌더에서 안 읽힌다**. 버튼 블록(`:~500-562`)은 무조건 승인/수정/거부 3개를 그린다. `execute_in_skill`(allowed=`[approve,reject]`)에도 수정 버튼이 떠서, 누르면 v3 resume 경로가 검증 없이 raw 전달(`InputRespondEntry.response: Any`) → 미들웨어 `_process_decision`에서 ValueError(`human_in_the_loop.py:343-349`).

---

## 4. 권장 설계

### 4.1 Edit-by-index (프론트가 도구 이름을 안 보낸다)
- **백엔드가 인덱스로 도구 이름을 채운다.** langchain은 decision↔action을 **positional index**로 매칭하고(`human_in_the_loop.py:438,450-455`) tool-call `id`는 미들웨어가 매칭된 pending call에서 가져온다. 따라서 `edited_action.name`은 백엔드가 **이미 알고 있는** `action_requests[index].name`으로 채울 수 있다.
- `conversation_agent_protocol_resume_redaction.py`가 **이미** 인터럽트별 원본 `{name,args}`를 인덱스로 재구성한다(`_raw_pending_actions_by_interrupt` `:69`, 매칭 `_restore_redacted_response` `:157`). 현재는 args만 복원하고 name은 프론트 값을 통과(`:184`). → name을 **권위적으로 덮어쓰기**.
- **결과:** 프론트는 `edited_action.name`을 신뢰성 있게 만들 필요가 없어진다 → §3.1 하드 중단 제거.

### 4.2 Field-based editor (raw JSON 대신 칸별 편집)
- `ArgsPreview`의 key/value 목록(이미 존재)을 **편집 가능한 폼**으로 확장: 각 값은 입력 컨트롤, **시크릿 키(`isSensitiveDisplayKey`)는 read-only 잠금**(`<redacted>` 표시, 편집 불가).
- submit 시 `JSON.parse` 없음 → §3.2 제거. 시크릿은 잠겨 rename/reorder 불가 → §3.3 누출/유실 제거. 프론트는 `restoreRedactedRecordPlaceholders` 불필요(백엔드가 복원 소유).

### 4.3 allowed_decisions 게이팅 (프론트 only)
- 카드가 받은 `allowed_decisions`대로 버튼 조건부 렌더. **빈/누락 시 기본 `[approve, reject]`**(edit 미포함 — `reviewForAction`의 fallback과 일치). 백엔드는 이미 올바른 값을 보내므로 **백엔드 변경 불필요**.

---

## 5. 파일 구조 (변경 대상)

### Backend
- **Modify** `backend/app/routers/conversation_agent_protocol_resume_redaction.py`
  - 모든 edit decision에 대해 action-by-index 해석 실행(현재 `<redacted>` 있을 때만 도는 early-return 완화)
  - `edited_action["name"]`을 `raw_actions[index]["name"]`으로 권위적 설정
- **Modify** `backend/app/routers/conversation_agent_protocol_commands.py`(선택)
  - `_handle_input_respond_command`에서 각 decision.type을 pending `review_configs[index].allowed_decisions`와 교차검증(조기 거절). 또는 `conversation_agent_protocol_resume.py:validate_resume_payload`.
- **Modify** `backend/tests/test_hitl_wire.py`
  - v3 `responses`-keyed resume + name-fill + 멀티액션 edit index 정렬 테스트 추가

### Frontend
- **Modify** `frontend/src/components/chat/tool-ui/approval-card.tsx`
  - 버튼을 `allowed_decisions`로 게이팅
  - edit: 하드 중단 제거 + field-based editor
  - `restoreRedactedRecordPlaceholders` 제거(백엔드 복원 소유)
- **Modify** `frontend/src/lib/types/index.ts` + `frontend/src/lib/chat/decision-mappers.ts`
  - `Decision.edited_action.name`을 optional로(또는 name-less edit 허용)
- **Modify** `frontend/src/components/chat/tool-ui/__tests__/approval-card.test.tsx`
  - allowed_decisions 게이팅 / name 없는 edit / 시크릿 잠금 / field editor 테스트

---

## 6. 구현 작업 (Task by Task)

> 권장 순서: **Task 1(테스트 먼저) → 2(백엔드 edit-by-index) → 3(프론트 게이팅) → 4(프론트 edit UI) → 5(통합검증)**. 각 Task는 독립적으로 그린이 되도록 구성.

### Task 1 — 백엔드 회귀 테스트를 먼저 추가한다 (TDD)

**Files:** `backend/tests/test_hitl_wire.py`

- [ ] **Step 1: v3 edit가 name 없이도 백엔드에서 채워지는 테스트(실패 예상).**
  `conversation_agent_protocol_resume_redaction.py`의 복원 함수를 직접 호출해, pending action(`action_requests=[{name:"execute_in_skill", args:{command:"old"}}]`)이 있을 때 decision `{type:"edit", edited_action:{args:{command:"new"}}}`(name 없음, redacted 없음)를 넣으면 결과가 `edited_action.name == "execute_in_skill"`, `args.command == "new"`가 되도록 단언.
  ```python
  def test_edit_decision_name_filled_from_pending_action_by_index():
      restored = restore_redacted_resume_payload(
          input_payload={"intr-1": {"decisions": [
              {"type": "edit", "edited_action": {"args": {"command": "new"}}}
          ]}},
          pending_actions_by_interrupt={"intr-1": [
              {"name": "execute_in_skill", "args": {"command": "old"}}
          ]},
      )
      d = restored["intr-1"]["decisions"][0]
      assert d["edited_action"]["name"] == "execute_in_skill"
      assert d["edited_action"]["args"]["command"] == "new"
  ```
  (실제 함수 시그니처/헬퍼 이름은 `conversation_agent_protocol_resume_redaction.py`를 열어 맞춘다 — `restore_redacted_resume_payload`(`:18`), `_raw_pending_actions_by_interrupt`(`:69`).)

- [ ] **Step 2: 멀티액션 edit가 index로 정렬되는 테스트(실패 예상).** action 2개일 때 decision 2개의 edit name이 각각 `action_requests[0].name`, `[1].name`으로 채워지는지.

- [ ] **Step 3: 실행해 실패 확인.**
  ```bash
  cd backend && uv run pytest tests/test_hitl_wire.py -q
  ```

### Task 2 — 백엔드: edit-by-index (name 채우기, 모든 edit에 적용)

**Files:** `backend/app/routers/conversation_agent_protocol_resume_redaction.py`

- [ ] **Step 1: early-return 게이트 완화.** 현재 `restore_redacted_resume_payload`는 `_resume_contains_redacted_edit(...)`가 False면 raw를 그대로 반환(`:24` 부근). edit decision이 하나라도 있으면(redacted 유무 무관) 인덱스 해석 경로를 타도록 조건을 확장한다. (redacted 없는 일반 edit도 name 채우기가 필요.)

- [ ] **Step 2: name 권위적 설정.** `_restore_redacted_response`(`:157-190` 부근)의 edit 분기에서, `raw_actions[index]`가 있으면:
  ```python
  edited = dict(decision.get("edited_action") or {})
  if index < len(raw_actions):
      edited["name"] = raw_actions[index]["name"]          # 권위적: 프론트 name 무시
      edited["args"] = restored_args                        # 기존 placeholder 복원 유지
  restored_decisions.append({**dict(decision), "edited_action": edited})
  ```
  - `raw_actions[index]`가 없을 때(방어)는 기존 동작(프론트 값 유지) fallback.
  - 기존 `<redacted>` placeholder 복원(`_restore_placeholders`)은 **그대로 유지**.

- [ ] **Step 3: Task 1 테스트 통과 확인.**
  ```bash
  cd backend && uv run pytest tests/test_hitl_wire.py -q && uv run ruff check app tests
  ```

- [ ] **Step 4 (선택, 조기 거절): allowed_decisions 검증.** `conversation_agent_protocol_commands.py:_handle_input_respond_command`(또는 `conversation_agent_protocol_resume.py:validate_resume_payload` `:64`)에서, 각 decision.type이 해당 인터럽트의 `review_configs[index].allowed_decisions`에 없으면 422/구조화 에러로 조기 거절. (지금은 미검증이라 미들웨어 깊은 곳에서 ValueError.) 프론트 게이팅(Task 3)이 1차 방어이므로 이건 방어층.

### Task 3 — 프론트: `allowed_decisions` 버튼 게이팅

**Files:** `frontend/src/components/chat/tool-ui/approval-card.tsx`, 테스트

- [ ] **Step 1: allow-set 유도.** `!submitting` 분기 상단(`:~501`)에서 1회 계산:
  ```ts
  const allowed = new Set(args?.allowed_decisions ?? [])
  const canApprove = allowed.size === 0 ? true : allowed.has('approve')
  const canEdit = allowed.has('edit')                      // 기본(빈) 시 edit 숨김
  const canReject = allowed.size === 0 ? true : allowed.has('reject')
  ```
  (빈/누락 → approve+reject만, edit 제외 — `reviewForAction` fallback과 동일 정책.)

- [ ] **Step 2: 각 버튼 그룹 가드.** 승인(`:~503-512`)은 `canApprove &&`, 수정(`:~515-538`)은 `canEdit &&`, 거부(`:~541-561`)는 `canReject &&`로 감싼다. reject-only(승인·수정 모두 불가)일 때 거부 confirm 2-step 유지.

- [ ] **Step 3: 테스트 추가.** `approval-card.test.tsx`:
  - `allowed_decisions: ['approve','reject']` → 수정 버튼 없음(`queryByText('edit')` null).
  - `['approve','edit','reject']` → 3개 모두.
  - 누락/`[]` → approve+reject만(edit 숨김).
  - reject-only → 거부만 + confirm 동작.
  - 실행: `cd frontend && pnpm exec vitest run src/components/chat/tool-ui/__tests__/approval-card.test.tsx`

### Task 4 — 프론트: 견고한 edit (하드 중단 제거 + field editor)

**Files:** `approval-card.tsx`, `decision-mappers.ts`, `types/index.ts`, 테스트

- [ ] **Step 1: 타입 완화.** `frontend/src/lib/types/index.ts`의 `Decision.edited_action`을 `{ name?: string; args: Record<string, unknown> }`로(name optional). `decision-mappers.ts`의 `toEdit`도 name 없이 호출 가능하게(또는 `toEditByIndex(args)` 추가).

- [ ] **Step 2: 하드 중단 제거.** `toDecision('modified', ...)`(`approval-card.tsx:~126-129`)에서 `if (!toolName) return null` 삭제. name은 있으면 advisory로 첨부, 없으면 생략(백엔드가 index로 채움). 호출부(`:~360-367`)의 `if (!standardDecision)` abort도 edit에선 불필요.

- [ ] **Step 3: field-based editor.** `ArgsPreview`(현재 key/value 목록)를 편집 모드 지원으로 확장하거나, 별도 `ArgsEditor` 컴포넌트 추가:
  - state를 `editedArgs: string`(JSON) → `draft: Record<string, unknown>`(키별 값)로 교체. 초기값 = `args.tool_args`(redacted).
  - 각 entry를 `<dt>{key}</dt><dd><input/></dd>`로. **`isSensitiveDisplayKey(key)`면 read-only 잠금**(`<redacted>` 표시, onChange 없음).
  - scalar는 텍스트 input, 비-scalar(object/array)는 compact JSON 텍스트 input(파싱 실패 시 해당 칸만 에러 표시 — 전체 abort 금지).
  - submit(`handleDecision('modified')`)은 `JSON.parse` 없이 `draft`를 직접 사용. `restoreRedactedRecordPlaceholders` 호출 제거(시크릿은 잠겨 변형 불가 → 백엔드가 복원).

- [ ] **Step 4: 죽은 코드 정리.** `restoreRedactedPlaceholders`/`restoreRedactedRecordPlaceholders`(`approval-card.tsx:56-80`)가 더 이상 안 쓰이면 제거. `editedArgs`/`jsonError` state 제거.

- [ ] **Step 5: 테스트 교체.**
  - 기존 "restores redacted placeholders…" 테스트(`:~294-350`)를 **삭제/교체**: un-redacted 주입을 멈추고, "시크릿 키는 read-only이며 편집 불가, 비-시크릿 칸만 수정해 제출하면 `<redacted>`가 리터럴로 안 나가고 name 없이 edit decision이 간다"를 단언.
  - **신규: name 없이 edit 동작**(§3.1 회귀). `tool_name` undefined로 렌더 → 한 칸 수정 → 제출 → `invalidJson` abort 없이 `{type:'edit', edited_action:{args:{...}}}`(name 없음/advisory) 전송 단언.
  - "renders tool args as a readable key/value list"(`:~208-237`)는 편집 컨트롤 추가에 맞춰 갱신.
  - 실행: `cd frontend && pnpm exec vitest run src/components/chat/tool-ui/__tests__/approval-card.test.tsx`

### Task 5 — 통합 검증

- [ ] **Step 1: 백엔드.**
  ```bash
  cd backend && uv run pytest tests/test_hitl_wire.py tests/test_hitl_middleware.py -q && uv run ruff check app tests
  ```
- [ ] **Step 2: 프론트.**
  ```bash
  cd frontend && pnpm exec tsc --noEmit && pnpm exec vitest run && pnpm lint
  ```
- [ ] **Step 3: 수동 시나리오.**
  - `execute_in_skill` 승인 카드 → **수정 버튼 없음**(allowed=approve,reject), 승인/거부만 동작.
  - `edit_file`/write 도구 승인 카드 → 수정 버튼 보임, 한 칸 수정 후 승인 → 모델이 수정된 인자로 실행.
  - 시크릿(api_key 등) 있는 도구 → 시크릿 칸 잠김, 비-시크릿 칸만 수정 가능, 제출 시 시크릿 정상 유지(백엔드 복원).
  - `tool_name`이 비는 슬롯에서도 edit이 `invalidJson` 없이 정상 제출.

---

## 7. (선택) 추가 워크스트림

본 작업의 핵심(§6)과 독립. 필요 시 별도 PR.

### 7.1 통합 "N건 대기" multi-action UX (프론트 only, 추가형)
현재: N개 카드 각각 렌더 + resume만 내부 배칭(코디네이터 이미 존재). 남은 건 **시각적 묶음**.
- `hitl_interrupt_id`로 같은 인터럽트의 카드를 그룹화하는 컨테이너(키: `args.hitl_interrupt_id`, 총수: `args.hitl_total_actions`). 현재 카드는 독립 tool-call 메시지로 방출되므로(`hitl-interrupts.ts:447-462`), 그룹 헤더("N건 대기")를 합성하거나 카드 묶음 래퍼가 필요.
- "모두 승인/모두 제출" 버튼 → 각 미결 action에 대해 `hitl.registerDecision(i, 기본결정)` 호출(기본은 카드별 allowed_decisions). 기존 coordinator가 배칭하므로 **백엔드/coordinator 변경 불필요, 순수 추가 UI**.
- i18n 키 신규(`chat.approval.pendingCount`, `chat.approval.approveAll`) — 현재 없음.
- 신규 컴포넌트 테스트(`hitl-coordinator.test.tsx` 슬롯 비어 있음).

### 7.2 부모 커스텀 HITL 정책의 linked subagent 전파 (백엔드)
현재: auto general-purpose subagent는 top-level 상속하지만, **linked(선언형) subagent는 자기 `middleware_configs`만** 읽어(`subagents.py:118`) 부모의 커스텀 `human_in_the_loop` override가 전파 안 됨. (자기 도구 기반 정책은 정상 적용 — 안전 측면 갭은 아니고, "부모 커스텀 정책 일관성" 이슈.)
- Fix point: `subagents.py:142-163` — 부모 정책/override를 child `components.interrupt_on`에 병합 후 `spec["interrupt_on"]` 설정.
- 테스트: `tests/agent_runtime/test_subagents_runtime.py`에 부모 override 전파 케이스.

### 7.3 (결정 필요) ask_user를 미들웨어 respond로 진짜 통합 vs 현행 유지
현재: write/skill/MCP = `HumanInTheLoopMiddleware`, ask_user = native `interrupt()` — wire에서만 통합. 두 메커니즘 공존.
- 옵션 A(권장, 저비용): **현행 유지 + 문서화.** ask_user는 native interrupt + `_interrupt_to_standard_chunk` 정규화로 충분히 동작. `runtime_component_builder.py:390-391`의 `interrupt_on["ask_user"]` 항목이 vestigial인지 확인 후, 유지(방어)할지 주석 명확화.
- 옵션 B(고비용): ask_user를 미들웨어 respond 경로로 이전(`tools/ask_user.py:121-163` + 정책 + `streaming` 어댑터). 단일 메커니즘이지만 UX/회귀 위험. **착수 전 별도 결정.**

---

## 8. 결정 스키마 / wire 계약 (구현 참조)

### Decision (프론트→백엔드, `HumanInTheLoopMiddleware` `HITLResponse.decisions[i]`와 1:1)
```ts
interface Decision {
  type: 'approve' | 'edit' | 'reject' | 'respond'
  edited_action?: { name?: string; args: Record<string, unknown> }  // ← 본 작업: name optional
  message?: string  // respond 필수, reject 선택
}
```
- **edit 계약(langchain 1.3.9, `human_in_the_loop.py:310-320`):** 미들웨어는 `edited_action["name"]`/`["args"]`를 hard subscript로 읽고 tool-call `id`는 매칭된 pending call에서 가져온다. decision↔action 매칭은 **positional index**. → 백엔드가 name을 index로 채우면 프론트는 name 불필요.

### 표준 interrupt payload (백엔드→프론트, SSE `interrupt` event)
```ts
type StandardInterruptPayload = {
  interrupt_id: string          // = str(intr.ns) (namespace)
  action_requests: Array<{ name: string; args: Record<string, unknown>; description?: string }>  // per-action id 없음 → index 참조
  review_configs: Array<{ action_name: string; allowed_decisions: Array<'approve'|'edit'|'reject'|'respond'> }>
}
```
- **allowed_decisions 출처:** Moldy `risk.py` → `interrupt_on` 정책 → langchain이 `review_configs`로 echo. 도구별 값은 §2.1 참조(`execute_in_skill`=approve,reject / `edit_file`=approve,edit,reject 등).
- **resume(v3):** `Command(resume={interrupt_id: {"decisions": [...]}})` — `conversation_agent_protocol_commands.py:_handle_input_respond_command`.

---

## 9. 권장 커밋 순서
1. `test(hitl): pin edit-by-index name-fill + multi-action edit ordering`
2. `fix(hitl): backend fills edited_action.name from pending action by index`
3. `fix(chat): gate approval-card buttons on allowed_decisions`
4. `fix(chat): robust approval edit — field editor + name-less edit, drop client redaction restore`
5. `test(chat): allowed_decisions gating + name-less edit + locked-secret`
6. (선택) `feat(chat): unified N-pending multi-action approval UX`

## 10. 완료 기준
- `execute_in_skill`(allowed=approve,reject) 카드에 **수정 버튼이 뜨지 않는다.**
- `edit_file`/write 도구에서 수정 후 승인이 동작하고, **`tool_name`이 비어도 `invalidJson` 없이** 정상 제출된다.
- 시크릿 키는 편집 카드에서 read-only, 제출 시 시크릿이 리터럴 `<redacted>`로 새지 않고 정상 유지된다(백엔드 복원).
- 프론트는 `edited_action.name`을 재구성하지 않아도 백엔드가 index로 채운다.
- `restoreRedactedRecordPlaceholders` 등 죽은 프론트 복원 코드가 제거된다.
- 백엔드 `test_hitl_wire.py`/`test_hitl_middleware.py` 그린, 프론트 vitest/tsc/lint 그린.
- 멀티액션 wire(§2.2)·subagent 상속(§2.1) 회귀 없음.

## 11. 리스크 / 주의
- **edit-by-index의 백엔드 early-return 완화**(Task 2 Step 1): redacted 없는 일반 edit도 인덱스 해석 경로를 타게 되므로, 기존 비-edit/respond resume 경로에 영향 없는지 회귀 테스트로 가드(`test_hitl_wire.py` 기존 케이스 유지).
- **field editor의 비-scalar 값**(중첩 object/array): 칸별 JSON 파싱 실패는 **해당 칸만** 에러 표시하고 전체 submit을 막지 않는다(§3.2 회귀 방지 의도 유지).
- **allowed_decisions fallback**은 반드시 `[approve, reject]`(edit 제외). edit를 fallback에 넣으면 edit-불가 도구에 다시 노출된다.
- approve/reject 경로(현재 정상)는 본 작업에서 동작 변경 없음 — 회귀 테스트로 확인.
</content>
