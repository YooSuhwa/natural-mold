# 도구 승인 모드 상세 기획 (에이전트 승인 단계, 실행 경로별 설정)

- 상태: **v1 초안**, 2026-11-08. 사용자가 방향(계층과 우선순위, 실행 경로별 모드, 1단계·2단계 분리)을 승인했다. 4장 결정 질문의 답을 받으면 v2로 확정하고 M1부터 구현한다.
- 범위: 도구 호출 전에 사람에게 묻는지를 정하는 규칙. 에이전트 모드(실행 경로별), 도구별 설정(항상 묻기/묻지 않기/조건부), 회사 상한, "이 대화 동안 허용",
  "실제 적용" 표, AI 검토(2단계). 승인함·승인 카드·무인 실행 대기·긴급 중지(순번 13)와 회사 도구 위험도 정책(순번 11)은 이미 있고 그 위에 얹는다.
- 이름: 메모리에도 "승인 모드"(기억 쓰기 승인 6종, `memory-policy-plan.md` Q10)가 있다. 화면과 코드에서는 이 기능을 **도구 승인 모드**(`tool_approval`)로 부른다.
- 근거: 이 대화의 리서치(부록 A), 순번 13 승인함, 가드레일 최소 기획(C5 도구 위험도), ADR-022(런타임 정책 수명), ADR-024(공통 실행 계약),
  현재 코드 조사(2장, 2026-11-08 `main` 19db8a58). 워크플로 실행 경로는 `workflow-plan.md`(PR #382) W0을 따른다.

## 1. 목표와 완료 기준

사람이 모든 위험 도구를 매번 승인하는 방식은 "승인 피로"로 승인이 형식이 되고(부록 A, Claude Code auto mode), 반대로 전부 자동은 되돌릴 수 없는 사고를 낸다.
목표는 **되돌리기 어려운 것만 사람에게, 나머지는 설정한 만큼 자동**으로 두되, 회사가 정한 바닥 아래로는 어떤 설정으로도 내려가지 못하게 하는 것이다.

완료 기준
1. 에이전트 설정에서 실행 경로(채팅·예약·워크플로·API)마다 모드를 고를 수 있고, 기본값으로는 지금 동작이 한 줄도 바뀌지 않는다.
2. 도구마다 항상 묻기 / 묻지 않기 / 조건부를 둘 수 있고, 조건부는 도구 인자에 대한 규칙(워크플로 판단 노드와 같은 엔진)으로 판정된다.
3. 회사 관리자가 위험도별 상한과 "항상 사람" 도구를 정하면, 그 아래로 푸는 설정은 저장·실행 모두에서 거부되고 화면에 잠겨 보인다.
4. 채팅에서 "이 대화 동안 허용"을 쓸 수 있고, 회사 상한과 강제 규칙을 넘지 못한다.
5. "실제 적용" 표가 도구 × 경로마다 결과(묻기/자동/대기/차단/숨김)와 이유 계층을 보여 주고, 실행은 같은 판정 함수로 같은 결과를 낸다.
6. 승인 카드와 감사 기록에 어느 계층 때문에 물었는지(또는 자동으로 넘겼는지)가 남는다.
7. (2단계) AI 검토 모드: AI가 허용/사람에게/거부를 판정하고 이유를 감사에 남기며, 차단이 반복되면 "모두 묻기"로 돌아간다.

## 2. 현재 코드 (조사 결과, 파일:줄)

**도구 위험도와 기본 승인**
- 위험도 5등급 `ToolRiskLevel`(`backend/app/tools/risk.py:16`)과 등급별 결정 종류(`risk.py:24`): 읽기 전용은 묻지 않고, 쓰기·외부 변경은 승인/수정/거절,
  코드 실행·알 수 없음은 승인/거절.
- 도구 하나의 기본 항목은 `interrupt_policy_for_tool`(`risk.py:279`), Deep Agents 파일 도구 기본값은 `default_deepagents_interrupt_policy`(`risk.py:271`).
- 에이전트의 명시 설정 합치기 `merge_interrupt_policies`(`risk.py:298`): "필수 승인은 끌 수 없다" — 기본 항목이 있는 도구에 `False`를 줘도 무시한다.
  **지금 도구별 "항상 묻기"는 되고, "묻지 않기"는 원래 안 묻는 도구에만 의미가 있으며, "조건부"는 사용자 설정으로는 없다.**
- 조립 `_build_interrupt_on_policy`(`backend/app/agent_runtime/runtime/interrupts.py:30-59`): 위험도 기본값 → `human_in_the_loop` 미들웨어 설정
  (`middleware_registry.py:79`, `interrupt_on: {도구명: true}`)을 합친다.

**회사 정책(가드레일 C5)**
- `tenants.settings["guardrails"]["tool_risk"]`: 위험도별 `allow`(승인 없음) / `approval`(승인 카드, 에이전트가 끌 수 없음) / `block`(도구 제거)
  (`backend/app/guardrails/tool_risk.py:1-12, 36`). 적용 `interrupts_for`(`tool_risk.py:126`): `allow`는 기본 항목만 지우고 에이전트가 요청한 승인은 남긴다.
  루트와 선언형 하위 에이전트에 모두 적용 `apply_to_agent`(`tool_risk.py:211`). 저장 위치 `CompanyPolicy.tool_risk`(`guardrails/policy.py:58-63`).
- 가드레일 승인 규칙(③): 도구 인자에 규칙이 지목한 값(개인정보 등)이 있으면 묻는다. `interrupt_on` 항목의 **`when` 술어**로 실린다
  (`guardrails/approval.py:57-80`). 재개 때 술어를 다시 부르므로 **같은 입력에 같은 답**이어야 한다(`approval.py:1-11`). 술어는 직렬화되지 않아
  사전 컴파일 실행(lease)은 이 규칙이 있으면 일반 경로로 돌아간다(`prepared_run_input_policy.py:15-24`, `deep_agent_factory.py:306-308`).
- 실행 조립 순서(`backend/app/agent_runtime/deep_agent_factory.py:328-354`): 회사 위험도 → 가드레일 승인 규칙 → 가드레일 스택 → 무인 처리.

**무인 실행**
- `unattended_interrupt_policy`(`risk.py:286`)와 `apply_unattended`(`backend/app/approvals/unattended.py:97`): `wait`는 표준 인터럽트로 승인함 대기,
  `block`은 `UnattendedApprovalMiddleware`(`unattended.py:49`)가 그 호출에만 BLOCKED 결과. `when` 술어도 존중한다(`unattended.py:65-67`).
- 대기/차단 선택은 예약 `agent_triggers.approval_policy`(기본 `inbox`, `models/agent_trigger.py:56`)와 배포 `agent_deployments.approval_policy`
  (기본 `blocked`, `models/agent_api.py:45`). 실행 설정 `AgentConfig.unattended_approval`(`runtime_config.py:162`).

**승인 요청과 긴급 중지**
- `approval_requests`(`models/approval_request.py`): `source` 검사 제약(35), `policy_version`/`policy_hash`(138-139), `reason`(142, 결정 사유),
  `actions`(134). 기록·결정은 `approvals/service.py`(`record_pending` 147, `claim` 245, 감사 `_audit` 386).
- 긴급 중지 `approvals/emergency.py:257`: 실행 접수와 MCP 호출에서 막는다. 이 기획의 모든 계층보다 앞선다.

**정책 고정**
- 에이전트별 버전 있는 런타임 정책 `RuntimePolicyV1`(`agent_runtime/runtime_policy.py:63`, 정규화 JSON과 해시), 대화마다 스냅숏
  `conversations.runtime_policy_snapshot`(`models/conversation.py:87`, ADR-022). 에이전트 사양 해시(`marketplace/agent_spec.py:240`)는
  `runtime_policy`를 포함한다(`agent_spec.py:107`) → 워크플로 Q1 지문 고정이 이 설정 변경도 잡는다.

**화면**
- 회사 위험도 폼 `frontend/src/features/guardrails/components/guardrail-tool-risk-form.tsx`, 에이전트 쪽 표시 `agent-guardrail-card.tsx`.
- 승인 카드 `frontend/src/components/chat/tool-ui/approval-card.tsx`(설명·사유 표시 34-83, 165), 묶음 카드 `grouped-approval-card.tsx`, 승인함
  `frontend/src/app/approvals/_components/approvals-page-client.tsx`.
- 예약·배포의 대기/차단 선택 `features/schedules/components/schedule-form.tsx`, `agents/[agentId]/settings/_components/right-panel/api-panel.tsx`.
- 에이전트 설정에 `human_in_the_loop` 전용 화면은 없고 일반 미들웨어 설정(`visual-settings-flow.tsx`, `agent-settings-draft.ts`)으로만 다룬다.

**워크플로 경로**
- 실행 경로는 `RunPath`(`agent_runtime/memory_paths.py:20`: chat/trigger/api/channel/subagent)와 `AgentRunSource`(`identity.py:27`). 워크플로 W0이
  `workflow`를 더한다. 워크플로 단계는 무인 실행 `unattended_approval="wait"`로 돈다(`workflow-plan.md` 3.4).

## 3. 설계

### 3.1 계층과 우선순위

위에서부터 판정하고, 처음 결론이 난 계층에서 멈춘다. **더 구체적인 설정이 이기되 1·2계층 아래로는 내려가지 못한다.**

| 순서 | 계층 | 누가 | 결과 |
|---|---|---|---|
| 1 | 긴급 중지, 회사 `block` | 회사 관리자 | 실행 거부 / 도구 숨김. 아래 어떤 설정도 못 바꾼다 |
| 2 | 회사 강제: 위험도 `approval`(강제), "항상 사람" 도구 목록, 가드레일 승인 규칙(③, 인자에 지목 값) | 회사 관리자 | **묻기**(AI 검토 건너뜀) |
| 3a | 도구별 "항상 묻기" | 에이전트 편집자 | **묻기**(AI 검토 건너뜀) |
| 3b | 도구별 "조건부" | 에이전트 편집자 | 규칙이 참이면 묻기, 거짓이면 4계층 상한 안에서 자동. 계산 오류는 묻기 |
| 3c | 도구별 "묻지 않기" | 에이전트 편집자 | 회사 상한 안이면 자동, 넘으면 묻기(이유 "회사 상한") |
| 4 | 이 대화 동안 허용(채팅만) | 채팅하는 사람 | 회사 상한 안이면 자동 |
| 5 | 에이전트 모드(실행 경로별) | 에이전트 편집자 | 모두 묻기(위험도 기본값) / 자동(상한 안 자동) / AI 검토(2단계) |

- **회사 상한(2계층 확장):** 위험도별 회사 동작에 네 번째 값 `approval_relaxable`(기본은 묻되 에이전트가 풀 수 있음)을 더한다. 지금 `approval`은 "강제"로 그대로 두고,
  기본값도 지금처럼 `approval`이다(Q2). 그래서 회사가 바꾸기 전에는 3c·4·5의 "자동"이 쓰기·외부 변경 도구에 적용되지 않는다.
- **무인 경로에서 "묻기":** 예약·API는 지금 설정(`approval_policy` inbox/blocked), 워크플로는 대기 고정. 묻는지 여부는 에이전트 설정, 묻는 방법은
  예약·배포 설정이 정한다(Q4).
- **하위 에이전트:** 위임된 호출도 같은 판정을 받는다. 하위 에이전트는 자기 도구 설정을 갖되 모드는 부모 실행의 경로를 따른다(지금 `apply_to_agent`·
  `apply_unattended`가 자식마다 적용하는 방식을 그대로 쓴다).

### 3.2 판정 함수 하나

`app/approvals/tool_modes.py`(신규)의 순수 함수 `decide(tool, path, settings, company, grants) -> ToolApprovalDecision(kind, layer, reason, decisions, rule)`.
- `kind`: `ask` / `auto` / `hidden` / `ai_review`(2단계). 무인 경로의 `ask`는 조립 단계에서 대기/차단으로 바뀐다(기존 `apply_unattended`).
- 실행 조립(`_build_interrupt_on_policy`와 `deep_agent_factory`의 회사·가드레일 단계)은 이 결과로 `interrupt_on`을 만들고, "실제 적용" API도 같은 함수를 부른다.
  `merge_interrupt_policies`·`interrupts_for`의 규칙은 이 함수 안으로 옮기고 기존 함수는 위임만 남긴다.
- 조건부와 가드레일 규칙은 `when` 술어로 싣는다. 조건부 규칙은 **데이터(JSON)**로 저장해 실행 쪽에서 술어로 바꾸므로, 이후 lease가 규칙 데이터를 실어
  일반 경로로 떨어지지 않게 할 수 있다(가드레일 술어와 달리 직렬화 가능).

### 3.3 설정 저장

- **에이전트:** `RuntimePolicy`에 `tool_approval` 섹션을 더한다(버전 2, Q1). 경로별 모드 `{chat, trigger, workflow, api}`와 도구별 항목
  `{도구명: {"mode": "always"|"never"|"when", "rule": {...}}}`. 런타임 정책이라 대화 스냅숏(ADR-022)에 고정되어 재개가 같은 설정을 쓰고, 에이전트 사양 해시에도 들어간다.
  기존 `human_in_the_loop.interrupt_on`은 읽을 때 "항상 묻기"로 옮겨 보이고, 저장하면 새 섹션으로 간다(이중 쓰기 없음, 마이그레이션 시험).
- **회사:** `tenants.settings["guardrails"]["tool_risk"]`에 `approval_relaxable` 값, 같은 묶음에 `always_ask_tools`(도구명 목록) 추가. 테이블 변경 없음.
- **이 대화 동안 허용:** 새 테이블 `conversation_tool_grants`(대화, 도구명, 허용한 사람, 시각, 철회 시각). 행 단위로 철회하고 감사한다(Q5).

### 3.4 조건부 규칙 (워크플로와 공유)

- 규칙 모양은 워크플로 판단 노드의 규칙 방식과 같다: 비교(`==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `contains`, `starts_with`, `ends_with`, `is_empty`), `all`/`any` 묶음, 깊이 3.
  값 참조는 도구 인자 경로(`args.to`, `args.amount`).
- 공유 모듈 `app/conditions/`(신규): 규칙 스키마 검증, 평가기, 사람이 읽는 요약("수신자가 @hancom.com으로 끝나면 묻지 않음"). 워크플로 W2와 이 기능 중 먼저
  들어가는 PR이 만들고 다른 쪽은 가져다 쓴다. 평가는 순수·결정적(재개 때 같은 답), 시간·난수·외부 호출 없음.
- 평가 오류(경로 없음, 타입 불일치)는 **묻기**. 결과와 규칙 요약은 승인 카드에 보인다.

### 3.5 화면

- **에이전트 설정 "도구 승인" 탭:** 위쪽은 경로별 모드(채팅·예약·워크플로·API), 아래는 도구 목록과 도구별 설정(항상 묻기/묻지 않기/조건부 + 규칙 편집기).
  회사가 잠근 칸은 자물쇠와 이유("회사 정책: 외부 발송은 항상 사람").
- **"실제 적용" 표:** 도구 × 경로 칸마다 결과 배지(묻기·자동·대기·차단·숨김)와 이유 계층. 칸을 누르면 판정 순서를 펼쳐 보인다.
- **회사 정책 화면:** 기존 위험도 폼에 "기본 묻기, 에이전트가 풀 수 있음"과 "항상 사람 도구" 목록 추가.
- **승인 카드:** 사유 한 줄("도구 설정: 항상 묻기", "조건: 수신자가 사외", "회사 정책"). 채팅 카드에 "이 대화 동안 허용" 버튼(허용 가능한 도구만).
  대화 상단에 허용 중인 도구 칩과 철회.
- 접근성·디자인·i18n 게이트는 기존 프론트 규칙을 따른다. 다크 모드 토큰만 쓴다.

### 3.6 기록과 감사

- 승인 요청에 `ask_layer`(계층 코드)와 `ask_reason`(요약)을 남긴다(`approval_requests` 열 추가).
- 감사 이벤트: `agent.tool_approval_updated`(설정 변경, 전·후 해시), `approval.auto_allowed`(위험 등급 도구가 자동으로 넘어간 호출, 계층 포함),
  `approval.grant_created`/`approval.grant_revoked`, (2단계) `approval.ai_reviewed`(판정·이유·모델). 내용 단언 시험을 `test_audit_integration.py`에 둔다.

### 3.7 AI 검토 (2단계)

- 모드 "AI 검토": 2계층·3a는 건너뛰고, 나머지 위험 호출을 검토 모델에 보낸다. 입력은 도구 이름·인자(가명화 후)·최근 사용자 요청 요약, 출력은
  `allow` / `ask` / `deny` + 이유. `deny`는 그 호출에만 BLOCKED 결과(에이전트가 다른 방법을 찾게)로 돌려준다.
- 차단이 연속 3회 또는 대화당 20회면 그 대화는 "모두 묻기"로 돌아간다(Claude Code auto mode의 방식).
- 검토 모델은 판단 모델(순번 22) 또는 가드레일 판정(순번 26)을 쓰고, 없으면 시스템 LLM 역할 `approval_review`로 지정한 모델. 검토 시간 상한과 실패 시 묻기.
- 평가 세트(사내 LiteLLM): 허용돼야 할 호출·물어야 할 호출·주입된 호출로 거짓 허용률을 재고, 기준을 넘지 못하면 기능을 켜지 않는다(메모리 평가와 같은 방식).

### 3.8 실행 계약 (ADR-024)

| 항목 | 이 기능의 값 |
|---|---|
| 실행 경로 | 모든 경로(chat·trigger·workflow·api, channel은 순번 24에서). 경로는 재개 체인의 첫 실행 기준(`origin_run_path`) |
| 신원과 회사 분리 | 판정은 실행 회사의 정책과 에이전트 설정만 읽는다. 대화 허용은 대화 소유자만 만들고 철회한다 |
| 회수 시점 | 회사 정책·에이전트 설정 변경은 다음 실행부터. 진행 중 실행은 대화 스냅숏(ADR-022)을 쓰되, 회사 `block`·긴급 중지는 즉시 |
| 버전 고정 | 에이전트 설정은 런타임 정책 해시, 승인 요청은 기존 `policy_hash`에 판정 근거 해시 포함 |
| 외부 부작용 | 자동으로 넘긴 위험 호출은 `approval.auto_allowed` 감사로 추적. 판정 자체는 부작용 없음 |
| 파일 수명 | 없음 |
| 사용량 | AI 검토 호출은 `run_source`를 따라 별도 사용 항목(`approval_review`)으로 센다 |
| 이전과 되돌리기 | 새 테이블 1개, 열 2개(NULL 허용). 되돌리면 모든 경로가 "모두 묻기"(지금 동작) |
| 외부 API | Agent API에는 노출하지 않는다(배포의 `approval_policy`만 유지) |

## 4. 결정 질문

| 번호 | 결정할 내용 | 선택지 | 추천 |
|---|---|---|---|
| Q1 | 에이전트 설정을 어디에 둘지 | A) `agents` 새 JSON 열 B) `RuntimePolicy` 새 섹션(버전 2) C) 새 테이블 | **B**. 이미 버전·정규화·해시·대화 스냅숏이 있어 재개와 워크플로 지문 고정이 그대로 된다 |
| Q2 | 회사 상한의 단위 | 위험도별만 / 위험도별 + 도구명 강제 목록 / 도구마다 전부 | **위험도별 4번째 값 `approval_relaxable` + "항상 사람" 도구명 목록**. 기본값은 지금처럼 `approval`(강제)이라 회사가 열기 전에는 동작이 같다 |
| Q3 | 1단계 모드 종류 | 모두 묻기 / 허용 목록 / 자동 (+2단계 AI 검토) | **모두 묻기와 자동 두 개로 줄인다**. "허용 목록"은 "모두 묻기 + 도구별 묻지 않기"와 같아서 두 길이 생기면 헷갈린다 |
| Q4 | 경로별 기본값과 무인 경로의 "묻기" | 전부 모두 묻기 / 경로마다 다르게 | **전부 모두 묻기(지금 동작)**. 무인 경로의 묻는 방법은 지금처럼 예약·배포의 `approval_policy`, 워크플로는 대기 고정 |
| Q5 | "이 대화 동안 허용"의 범위 | 도구명 단위 / 도구명+인자 조건 / 없음 | **도구명 단위, 그 대화 안에서만, 재개와 이후 턴에도 유지**. 쓰기(내부) 등급은 기본 허용, 외부 변경·코드 실행은 회사가 상한을 연 경우만. 회사 강제·항상 묻기 도구는 불가, 에이전트 도구 승인 설정이 바뀌면 자동 철회 |
| Q6 | 조건부 규칙 표현 | 코드 술어 / JSON 규칙(워크플로와 공유) / CEL 같은 식 언어 | **JSON 규칙, `app/conditions/` 공유**. 결정적이고 직렬화되며 화면 편집기와 요약을 워크플로와 함께 쓴다. CEL은 순번 10이 2단계로 미뤘다 |
| Q7 | AI 검토 모델 | 판단 모델(22) / 가드레일 판정(26) / 일반 LLM | **판단 모델이 있으면 그것, 없으면 시스템 LLM 역할 `approval_review`**. 평가 세트 기준을 통과한 모델만 켠다. 2단계라 22번 뒤 |
| Q8 | 워크플로에서의 설정 | 에이전트 설정의 workflow 칸 / 노드마다 덮어쓰기 | **에이전트의 workflow 칸만. 노드는 더 엄격하게(모두 묻기 강제)만 가능, 1차에는 노드 덮어쓰기 없음** |
| Q9 | 자동으로 넘긴 위험 호출 기록 | 전부 감사 / 표본 / 안 남김 | **위험 등급(쓰기 이상) 호출은 전부 `approval.auto_allowed`**. 읽기 전용은 남기지 않는다 |

## 5. PR 순서

| PR | 내용 | 완료 확인 |
|---|---|---|
| M1 | 판정 함수 `tool_modes.decide`, 기존 `merge_interrupt_policies`·`interrupts_for` 규칙 이관(동작 동일), `RuntimePolicy` v2 `tool_approval` 섹션(경로별 모드만), 회사 `approval_relaxable`·`always_ask_tools` | 기존 승인 시험 전부 그대로 통과, 계층 우선순위 표 시험, 정책 v1→v2 읽기 시험 |
| M2 | `app/conditions/` 규칙 엔진(워크플로 W2보다 먼저면 여기서), 도구별 항상/묻지 않기/조건부, "실제 적용" API, `approval_requests.ask_layer/ask_reason`(마이그레이션), 감사 | 규칙 연산자·오류=묻기 시험, 재개 결정성, lease/일반 경로 결과 동일, 실제 적용 API == 실행 판정(독립 기대값) |
| M3 | 화면: 에이전트 "도구 승인" 탭, 실제 적용 표, 회사 폼 확장, 승인 카드 사유 | vitest, 접근성·i18n·다크 모드 캡처 |
| M4 | 이 대화 동안 허용: `conversation_tool_grants`, 카드 버튼·칩·철회, 감사 | 회사 상한 초과 거부, 강제 도구 불가, 설정 변경 시 자동 철회, 남의 대화 404 |
| M5 (2단계) | AI 검토 모드, 연속 차단 시 되돌림, 평가 세트와 기준 | 거짓 허용률 기준 통과(사내 LiteLLM), 실패·지연 시 묻기 |

워크플로 경로(`workflow`)는 W0이 먼저 머지되면 M1에 칸을 넣고, 아니면 W0이 넣는다. 마이그레이션 번호는 머지 순서대로 잡는다(워크플로 i26 예약).

## 6. 검증

- **우선순위 표 시험:** 계층 쌍마다(회사 강제 vs 묻지 않기, 항상 묻기 vs 자동 모드, 조건 거짓 vs 회사 상한, 대화 허용 vs 강제 도구 …) 기대값을 표로 고정한다.
- **mutation 확인:** 계층 하나씩 빼거나 순서를 바꾼 변형을 주입해 표 시험이 실패하는지 확인한다. 특히 "필수 승인은 끌 수 없다"를 지운 변형.
- **독립 기대값:** "실제 적용" API와 실행 판정은 같은 함수를 쓰므로 서로 비교하지 않고 각각 명시 기대값으로 고정한다(CLAUDE.md 위임 등가성 규칙).
- **경로:** 채팅(카드), 예약 inbox/blocked, 워크플로 대기, API blocked, 하위 에이전트 위임 — 각 경로에서 같은 도구가 설정대로 묻거나 넘어가는지.
- **재개:** 승인 후 재개에서 조건부·가드레일 술어가 같은 답을 내는지, 대화 허용이 재개에 유지되는지.
- **보안:** 주입된 지시로 "이 대화 동안 허용"된 도구를 악용하는 시나리오(외부 발송이 기본으로 허용 불가인지), 남의 에이전트·대화 설정 404, 권한 검사 비율 스크립트.
- 기존 게이트: authz 비율, 라우트 수·OpenAPI 스냅숏, 문서 목록, pyright(바뀐 파일을 import하는 파일까지), PG 레인(마이그레이션).

## 7. 위험

| 위험 | 대응 |
|---|---|
| 자동 설정이 넓어져 사고 | 기본값은 지금 동작, 회사가 상한을 열기 전에는 쓰기 이상 자동 불가, 자동 위험 호출 전부 감사 |
| "이 대화 동안 허용" 악용(프롬프트 주입) | 외부 변경·코드 실행은 기본 불가, 강제 도구 불가, 칩으로 보이고 언제든 철회 |
| 조건부 술어 때문에 lease 경로 이탈 | 규칙을 데이터로 저장해 lease가 실을 수 있게 하고, 그 전까지는 일반 경로로 돌아가는 기존 방식(가드레일과 같음) |
| 판정 규칙이 여러 함수에 흩어져 어긋남 | `tool_modes.decide` 하나로 모으고 기존 함수는 위임만 |
| 메모리 "승인 모드"와 이름 혼동 | 화면·코드 이름을 "도구 승인"으로 분리 |
| AI 검토의 거짓 허용 | 2단계로 분리, 평가 기준 미달 시 켜지 않음, 실패·지연은 묻기, 연속 차단 시 되돌림 |

## 부록 A. 리서치 (2026-11-08)

| 제품 | 승인 단계 |
|---|---|
| Claude Code | 모드: 묻기 / 편집 자동 / auto(분류 모델이 허용·차단) / 모두 허용. auto는 승인 피로를 줄이려고 나왔고([Anthropic](https://www.anthropic.com/research/claude-code-auto-mode), [permission modes](https://code.claude.com/docs/en/permission-modes)), 연속 3회·세션 20회 차단 시 수동으로 돌아간다는 해설([almanac](https://claude-almanac.pages.dev/docs/auto-mode)) |
| Codex CLI | 승인 정책(`untrusted`/`on-request`/`never`)과 샌드박스 범위를 따로 둔다([sandboxing](https://developers.openai.com/codex/concepts/sandboxing), [해설](https://codex.danielvaughan.com/2026/03/26/codex-cli-approval-modes-sandbox-security)) |
| Cursor | 자동 검토(기본, 분류 모델·샌드박스) / 허용 목록 / 모두 실행([terminal](https://cursor.com/help/ai-features/terminal)) |
| Copilot Studio | 도구마다 승인 필요, 결정은 승인 / 이 세션 동안 승인 / 거부([RM570434](https://mc.merill.net/message/RM570434)). 흐름 안 다단계 승인에 AI 단계와 사람 단계([multistage](https://learn.microsoft.com/microsoft-copilot-studio/flows-advanced-approvals)) |
| ServiceNow | 도구마다 supervised/autonomous, 무인 실행에서 supervised 도구는 멈춘다([autonomous-supervised](https://raw.githubusercontent.com/ServiceNow/ServiceNowDocs/australia/markdown/it-service-management/autonomous-supervised.md), [AI agent action](https://www.servicenow.com/docs/r/build-workflows/workflow-studio/use-an-ai-agent-action.html)) |
| Bedrock Agents | 동작마다 사용자 확인, 프롬프트 주입 방어 목적([user confirmation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html)) |
| OpenAI Agents SDK / Google ADK | 도구에 `needs_approval` / `require_confirmation`: 참·거짓 또는 호출마다 판정하는 함수([Agents SDK](https://openai.github.io/openai-agents-python/human_in_the_loop), [ADK](https://google.github.io/adk-docs/tools-custom/confirmation)) |
| Agentforce | 액션 보안: 직원용은 사용자 신원, 서비스용은 에이전트 신원, 민감 액션은 사용자 확인([secure actions](https://developer.salesforce.com/docs/atlas.en-us.packagingGuide.meta/packagingGuide/secure_agentforce_actions.htm)) |
| CSA 자율성 단계 | 0~5단계와 단계별 통제, 사람이 매번 승인 → 사람이 감시로 이동, 되돌리기 어려울수록 사람([CSA](https://cloudsecurityalliance.org/blog/2026/01/28/levels-of-autonomy), [해설](https://www.bolddesk.com/blogs/ai-agent-autonomy-levels)) |

공통점: (1) 에이전트·세션 단위 모드, (2) 도구별 항상/안 함/조건부 덮어쓰기, (3) AI가 먼저 보고 애매한 것만 사람, (4) 세션 동안 허용, (5) 무인 실행은 대기 또는 차단.
확인하지 못한 것: Claude Code 차단 횟수 기준(해설 글만), Codex `untrusted` 세부 동작(공식 설정 문서 미확인), Agentforce `require_user_confirmation`의 현재 화면.
