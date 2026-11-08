# 순번 23 워크플로 1차 핵심 노드 상세 기획 (E1, 기획서 v2 22장 확장)

- 상태: **v2 확정**, 2026-11-08. 결정 질문 Q1~Q9(4장)는 모두 추천안으로 확정했다. W0부터 구현한다. 편집 화면 디자인 계층과 다크 모드는 3.7 끝과 부록 C.
- 범위: 기획서 22.9절 "워크플로 1차 핵심"만. 핵심 7종(시작 입력, 에이전트, 판단 true/false 규칙 방식, 승인, 변환과 템플릿,
  최종 출력, 실패 종료)으로 실제 업무 하나를 설계·실행·중단·복구·감사까지 검증한다. 나머지 1차 13종, 2차, 3차는 이 문서 범위 밖이다.
- 근거: 기획서 v2 22장(22.1~22.9), 34장 D32~D35, 32장 검증 표(워크플로 복구·데이터 계약), ADR-024(공통 실행 계약),
  현재 코드 조사(2장, 2026-11-08 `main` 19db8a58).

## 1. 목표와 완료 기준

워크플로는 이미 만든 에이전트들을 노드로 이어 정해진 순서로 실행하는 별도 리소스다. 바깥 흐름(순서, 입력 전달, 분기, 승인, 실패 종료)은
워크플로가 고정하고, 에이전트 단계 안에서는 각 에이전트가 기존 도구와 정책으로 스스로 실행한다. 하위 에이전트(모델이 실행 중 위임을 고름)와 다르다.

완료 기준(기획서 22.9 "워크플로 1차 핵심"):

1. 실제 업무 1개(Q3)를 화면에서 설계하고 게시해 실행하면, 단계 기록·선택된 경로·당시 버전·승인 대기가 실행 상세에 보인다.
2. 중단과 복구: 업무 승인 대기, 단계 안 도구 승인 대기, worker 교체 뒤 같은 지점부터 재개되고 내부 단계가 두 번 실행되지 않는다.
3. 다음 상황에서 잘못된 단계가 실행되거나 저장되지 않는다: 단계 중 에이전트 보관, 승인 대기 중 권한 회수, 중복 승인 콜백, 긴급 중지.
4. 에이전트 단계가 끝났는지 알 수 없게 멈추면(실행 중 worker 종료) 다시 실행하지 않고 **결과 불명**으로 멈춰 사람 확인으로 보낸다.
5. 생성·게시·실행·승인·취소·실패가 감사 기록에 내용과 함께 남는다.
6. 화면만 완성된 상태는 완료로 보지 않는다(기획서 22.9).

## 2. 현재 코드 (조사 결과, 파일:줄)

**실행 접수와 실행 행**
- `accept_owned_execution` (`services/agent_execution_acceptance.py:216`)은 `source`를 `Literal["chat","api","trigger"]`(44)로, 봉투 종류를
  `"api"|"trigger"`(45)로 닫아 둔다. 귀속 표(`tenancy/attribution.py:34-40`)도 같은 세 값이다. 워크플로는 세 곳에 `workflow`를 더해야 한다.
- 봉투(envelope)는 잠금 뒤 `conversation_run_id IS NULL`과 대화·사용자·에이전트 일치를 검사하고(121-191), 실행 생성 뒤 `_bind_envelope`(195-205)가
  봉투에 실행 ID를 묶고 상태를 투영한다(interrupted→waiting). 예약 실행의 `AgentTriggerRun`이 이 패턴이다. **워크플로 단계 실행도 같은 봉투로 둔다.**
- 대화당 활성 실행 1개를 앱(`conversation_run_creation.py:192`)과 DB 부분 유일 색인(`uq_conversation_runs_active_conversation`)으로 막는다.
  그래서 에이전트 단계마다 **내부 대화**가 필요하다.
- 실행 상태: queued, running, interrupted, canceling, canceled, completed, failed, stale(`conversation_run_creation.py:56-76`).

**예약 실행(가장 가까운 선례)**
- `execute_trigger`(`agent_runtime/trigger_executor.py:171`) = 확보 → 대화 준비 → invoke → 전달. invoke 모드(`agent.ainvoke`), 무인 승인은
  `unattended_approval="wait"`(승인함 대기) 또는 `"block"`(`trigger_agent_config.py:75`).
- 승인 대기면 `pause()`가 실행을 interrupted로 바꾸고 `RunOrigin(source="trigger", trigger_id, trigger_run_id)`로 승인 요청을 남긴다
  (`approvals/recording.py:71-142`). 승인함 결정은 채팅 재개 경로로 이어 실행하고(`routers/conversation_protocol_fork.py:198-273`),
  재개 실행이 끝나면 `settle`이 예약 실행 행을 마무리한다(`approvals/triggers.py:74-104`). **워크플로 단계도 같은 대기·재개를 쓰고,
  settle에서 바깥 흐름을 다시 깨운다.**

**작업 큐와 체크포인트**
- 작업 큐(`app/jobs`): SKIP LOCKED 확보, 리스 갱신, 취소 플래그, 재확보 시 처리기를 처음부터 다시 부름(체크포인트 열 없음, `models/job.py:52-75`).
  → 바깥 흐름의 재개 지점은 **LangGraph 체크포인트**로 만든다.
- 체크포인터는 전역 `AsyncPostgresSaver` 하나(`agent_runtime/checkpointer.py:112`). 채팅·예약·API 모두 `thread_id = 대화 ID`.
  워크플로 바깥 그래프는 대화가 아니므로 `thread_id = "workflow:<run_id>"`로 분리한다(기획서 22.7 "분리하는 것").

**에이전트 설정과 버전**
- 에이전트에는 리비전·게시 개념이 없다. 대화에 고정되는 것은 `runtime_policy`(파일·할 일·요약)뿐이고, 지침·모델·도구·스킬은 실행마다 살아 있는
  행에서 읽는다(`services/conversation_agent_context.py:38-173`, ADR-024 4절).
- 에이전트 설정을 불변 스냅숏으로 직렬화하는 코드는 마켓플레이스에 있다(`marketplace/agent_spec.py:240 build_agent_spec_payload`).

**구조화 출력**
- 에이전트 모델·런타임에 출력 스키마가 없다. `create_deep_agent`는 `response_format`을 받고(`deepagents/graph.py:301`), 준비된 하위 실행에만
  쓰는 코드가 있다(`agent_runtime/prepared_response_variants.py`, `prepared_task_tool_handler.py:99-106`의 `structured_response`).

**권한**
- OpenFGA 모델에 `type workflow`(owner, manager, editor, consumer, can_run/edit/share/revoke/delete)가 이미 있고(`authz/model.fga:200-212`),
  `RESOURCE_TYPES`에도 있다(`models/authz.py:44`). 수명 훅(`authz/resource_lifecycle.py:55-63`)에는 아직 없다.
- 보관된 에이전트가 트리에 있으면 실행을 거부한다(`authz/agent_execution_scope.py:41-58`, `AGENT_ARCHIVED` 409).
- 이름 충돌: Dify 호환 `POST /workflows/run`(`routers/agent_runtime_api.py:729-760`, 에이전트 하나 실행)이 이미 있다. 관리 API는 `/api/workflows`로 둔다.

**화면**
- `@xyflow/react` 12.10.2(`frontend/package.json:59`), 자동 배치 라이브러리 없음. 에이전트 비주얼 설정이 읽기 전용 흐름 화면
  (`components/agent/visual-settings/visual-settings-flow.tsx`, 노드 7종, 고정 좌표, `next/dynamic ssr:false`).
- 사이드바 항목은 `components/layout/app-sidebar.tsx:51-56`(템플릿·마켓플레이스·승인함). 승인함 화면(`app/approvals/_components/approvals-page-client.tsx`),
  예약 실행 목록(`features/schedules/components/schedule-run-list.tsx`), 추적 화면(`components/chat/trace-debugger-view.tsx`)을 재사용할 수 있다.

## 3. 설계

### 3.1 테이블 (마이그레이션 i26)

| 테이블 | 핵심 열 | 비고 |
|---|---|---|
| `workflows` | org·tenant·user(소유자), name, description, draft_definition(JSON), draft_revision, published_version_id, archived_at | `RequiredOrgScope`. 초안은 저장할 때마다 revision 증가(동시 편집 충돌 409) |
| `workflow_versions` | workflow_id, version_no, definition(JSON, 불변), definition_hash, agent_pins(JSON: 노드별 에이전트 ID·고정값, Q1), published_by/at | 게시마다 1행, 고치지 않음 |
| `workflow_runs` | workflow_id, version_id, 실행자(user), status, input(JSON), output(JSON), error_code, current_node, job_id, started/finished_at, cost 집계 | 상태: queued, running, waiting, unknown, completed, failed, canceling, canceled |
| `workflow_step_runs` | run_id, node_id, node_type, attempt, status, input(JSON), output(JSON), branch, conversation_id, conversation_run_id(유일), error_code, started/finished_at | 에이전트 단계의 **봉투**. 상태: queued, running, waiting, completed, failed, unknown, skipped, canceled |

- **W1(i27)은 `workflows`·`workflow_versions`만** 만든다. `workflow_runs`·`workflow_step_runs`와 아래 열 추가는 실행기와 함께 W2에서 한다. `workflows`는 게시 버전을 FK 대신 `published_version_no`로 가리킨다(두 테이블이 서로 참조하는 순환을 피함).
- `conversations`에 `workflow_step_run_id`(NULL 허용, FK)를 더해 내부 대화를 표시한다. 대화 목록·검색·내비게이터·공유·메모리 회상 등 **모든 대화 조회 경로**에서
  숨긴다(CLAUDE.md "히든 리소스 필터는 모든 resolution 경로에" 규칙, 전수 grep으로 형제 경로 확인).
- `approval_requests.source`에 `workflow`를 더하고 `workflow_run_id`, `workflow_node_id` 열을 둔다(업무 승인과 단계 안 도구 승인 모두).
- `conversation_runs.run_source`·귀속에 `WORKFLOW`를 더한다(사용량·통계 귀속).

### 3.2 정의 스키마 `moldy.workflow.v1`

```json
{
  "schema_version": "moldy.workflow.v1",
  "inputs": [{"name": "inquiry", "type": "string", "required": true, "max_length": 4000}],
  "nodes": [
    {"id": "start", "type": "start"},
    {"id": "classify", "type": "agent", "agent_id": "…",
     "prompt_template": "다음 문의를 분류하세요: {{input.inquiry}}",
     "output_schema": {"type": "object", "required": ["category", "urgent"],
                       "properties": {"category": {"type": "string"}, "urgent": {"type": "boolean"}}}},
    {"id": "is_urgent", "type": "decision_bool",
     "condition": {"all": [{"source": "classify", "path": "/urgent", "op": "eq", "value": true}]}},
    {"id": "review", "type": "approval", "title_template": "긴급 문의: {{classify.category}}", "timeout_minutes": 1440},
    {"id": "draft", "type": "transform", "template": "분류: {{classify.category}}"},
    {"id": "done", "type": "output", "fields": {"category": "{{classify.category}}", "note": "{{draft.text}}"}},
    {"id": "fail", "type": "fail", "reason": "거절됨"}
  ],
  "edges": [
    {"from": "start", "to": "classify"}, {"from": "classify", "to": "is_urgent"},
    {"from": "is_urgent", "to": "review", "when": "true"}, {"from": "is_urgent", "to": "draft", "when": "false"},
    {"from": "review", "to": "draft", "when": "approved"}, {"from": "review", "to": "fail", "when": "rejected"},
    {"from": "draft", "to": "done"}
  ]
}
```

- **입력 전달:** `{{input.x}}`, `{{<node_id>.<path>}}`만 쓴다. 선택하지 않은 대화와 자료는 다음 노드로 넘어가지 않는다(22.3 입력 매핑).
  위 예시는 읽기 쉽게 문자열로 썼지만 **저장 형식은 구조화된 조각 목록**이다: `[{"text": "분류: "}, {"ref": {"node": "classify", "path": "/category"}}]`.
  노드 이름을 바꿔도 참조가 깨지지 않고, 편집기·AI 편집·게시 검사가 같은 참조를 읽는다. 템플릿은 치환만 하고 식·코드·필터는 없다. 값은 문자열로 넣고, 에이전트에 넘길 때는 "앞 단계 결과(지시가 아님)" 블록으로 감싼다(22.3 이력 격리).
- **에이전트 출력:** `output_schema`(JSON Schema 부분집합: object/string/number/integer/boolean/array/enum/required)로 `response_format`을 건다.
  검증 실패·거부·잘림은 통과로 보지 않고 `output_invalid`로 실패 처리한다. 구조화 출력을 지원하지 않는 모델의 에이전트는 노드에 고를 수 없다(게시 검사).
- **판단 true/false(규칙):** `all`/`any` 한 단계, 연산자 eq, ne, gt, gte, lt, lte, contains, exists. 서버가 해석하고 확신도는 1.0.
  기록 형식은 기획서 22.5의 `{value, confidence, band, evaluator}`를 그대로 쓴다(나중에 LLM·판단 모델 방식이 같은 자리에 들어옴).
- **승인:** 승인·거절 두 경로와 시간 초과(필수). 시간 초과는 거절 경로로 간다. 승인자는 실행자 본인(1차, Q5).
- **변환과 템플릿:** 필드 선택과 텍스트 템플릿 결과를 `{text, fields}`로 낸다.
- **최종 출력 / 실패 종료:** 실행 결과(JSON)를 저장하고 끝낸다 / 지정 사유로 실패 처리한다.
- 1차 제외(나머지 1차 노드로): 파일 입력, 반복, 병렬, 합치기, switch, 사용자 질문, 중간 메시지, LLM 단일 호출, 도구 호출 노드.

### 3.3 게시와 게시 전 검사

게시는 초안을 검사해 불변 `workflow_versions` 행으로 고정한다. 서버 검사(기획서 22.7):
연결 끊긴 노드, 순환(1차는 반복 노드가 없으므로 순환 전부 금지), 시작 1개·끝(출력/실패) 1개 이상, 판단·승인 노드의 경로 누락, 시간 제한 없는 승인,
템플릿이 존재하지 않는 노드·경로·입력을 참조, 참조 에이전트의 존재·보관 여부·실행 권한(`can_run`), 구조화 출력 지원 모델, 노드 수 상한(50).
에이전트 고정 방식은 Q1.
- **참조는 지배 노드만**(W1에서 정함): 템플릿·조건이 참조하는 노드는 시작에서 이 노드로 오는 **모든 경로**에서 앞에 있어야 한다. 승인 경로에서만 실행되는 노드를
  "아니오" 경로와 합쳐지는 뒤 노드가 참조하면 실행 때 값이 없으므로 `ref_not_upstream`으로 막는다.
- **에이전트 지문**(Q1 구현): 마켓플레이스용 `build_agent_spec_payload`는 비밀값이 있으면 오류를 내고 ID를 빼므로 쓰지 않는다. 실행에 영향을 주는 에이전트 열과
  설정 연결 행(도구·스킬과 스킬 내용 해시·MCP 도구·하위 에이전트·가드레일 연결·메모리 설정)을 정렬해 해시한다(`app/workflows/pins.py`).
  `agents`에서 CASCADE로 이어지는 테이블은 모두 "고정"이나 "제외"로 분류해야 하고, 분류되지 않은 새 테이블은 테스트가 막는다(지식 폴더·도구 승인 모드 연결이 빠지지 않게).

### 3.4 실행 구조

```
POST /api/workflows/{id}/runs  → workflow_runs(queued) + 작업 "workflow.run" (멱등 키 = run_id)
worker: workflow.run 처리기
  └ workflow_compiler: 게시 버전 → LangGraph StateGraph (노드 = 함수 노드, 판단 = 조건부 연결이 value만 읽음)
  └ 바깥 체크포인트 thread_id = "workflow:<run_id>"  (재확보되면 마지막 체크포인트부터)
  └ 에이전트 노드: WorkflowAgentInvoker
       1) workflow_step_runs(queued) + 내부 대화 생성(이미 있으면 재사용)
       2) accept_owned_execution(source="workflow", envelope=("workflow", step_run.id))
       3) invoke 모드 실행, unattended_approval="wait", run_path="workflow"
       4) structured_response 검증 → step output 저장 → 다음 노드
```

- **진행 기록은 단계 행 하나**(W2a에서 정함): 바깥 그래프를 LangGraph 체크포인트로 따로 저장하지 않는다. 1차는 반복·병렬이 없어 실행은 항상 한 줄이므로,
  마지막으로 끝난 단계와 그 단계가 고른 갈래로 다음 노드가 정해진다. 재확보된 작업은 단계 행만 읽고 이어 가며, 체크포인트와 단계 행의 순서 문제가 생기지 않는다.
  반복·병렬 노드가 열리는 확장 단계에서 그래프 실행기(StateGraph)를 다시 검토한다.
- **내부 대화**는 `source="workflow"`로 만든다. 대화 목록·내비게이터는 `source="ui"`만 보여 주므로(예약·API 대화와 같은 방식) 대화 테이블에 열을 더하지 않는다.
- **에이전트 단계는 예약 실행과 같은 경로를 쓴다.** 대화용 HTTP 엔드포인트를 단계마다 부르지 않는다(22.7). 사용자·모델·도구·스킬·자격 증명·가드레일 정책은
  에이전트 단독 실행과 같고, 실행 신원은 워크플로 실행자다.
- **업무 승인 노드:** 바깥 그래프가 LangGraph `interrupt`로 멈추고, `approval_requests(source="workflow", workflow_run_id, workflow_node_id)`를 남긴 뒤
  실행 상태를 `waiting`으로 둔다. 승인함 결정은 compare-and-set(`approvals/service.py:245-307`)으로 한 번만 반영되고, 결정이 나면 `workflow.run` 작업을
  다시 넣어 같은 체크포인트에서 이어 간다. 단계 안 도구 승인과 화면에서 구분해 보인다(22.7 승인 구분).
- **단계 안 도구 승인:** 에이전트가 도구 승인으로 멈추면 단계는 `waiting`, 실행도 `waiting`. 승인함 결정 → 채팅 재개 경로로 단계 실행이 이어지고,
  `approvals/waiters.py`의 settle에 워크플로 처리기를 더해 단계 결과를 저장하고 `workflow.run`을 다시 넣는다.
- **한도:** 실행당 에이전트 단계 수(50), 실행 시간(기본 60분, 승인 대기 시간 제외), 실행 전·단계 전 지출 한도(`UsageLimits.blocking`).
  넘으면 사유를 남기고 실패 종료한다.
- **취소:** 실행 취소 → 진행 중 단계 실행에 기존 취소(`request_cancel_run`)를 전하고, 대기 중 승인 요청을 취소하고, 작업 취소 플래그를 세운다.
  긴급 중지(`approvals/emergency.py`)는 워크플로 실행도 멈추고 새 실행을 거부한다.

### 3.5 복구와 결과 불명 (ADR-024 5절)

| 멈춘 지점 | 재확보 뒤 처리 |
|---|---|
| 노드 시작 전 | 마지막 체크포인트부터 정상 진행 |
| 에이전트 단계 접수 전(step queued, 실행 없음) | 그대로 접수 |
| 에이전트 단계 실행 중(실행 행이 running/stale) | **다시 실행하지 않는다.** 실행이 completed로 저장됐으면 그 결과를 쓰고, 아니면 단계·실행을 `unknown`으로 두고 승인함 담당자 확인 요청을 낸다(계속 진행/실패 처리 선택). **W4a 구현:** 요청은 `kind=business`, 동작 이름 `workflow_unknown_check`, 기한 7일. 승인 = 같은 단계를 **새 내부 대화에서 다시 실행**(앞선 대화·실행 ID는 단계 `input.previous_attempts`에 남음), 거절·만료 = 실행 실패(`step_outcome_unknown`). 결과 불명은 실행 종료가 아니므로 `workflow.run_finish`가 아니라 `workflow.step_unknown`을 남기고, 취소하면 확인 요청도 취소된다 |
| 단계 결과 저장 뒤, 다음 노드 전 | 저장된 단계 결과로 이어 감(같은 노드를 다시 부르지 않음) |
| 승인 대기 | 결정이 날 때까지 그대로, 결정 뒤 이어 감 |

- 단계 결과 저장과 바깥 체크포인트 사이 순서: **단계 행 저장(commit) → 체크포인트**. 재개 때는 체크포인트보다 단계 행을 먼저 본다. 같은 단계의 두 번째
  접수는 봉투의 `conversation_run_id IS NULL` 검사(`agent_execution_acceptance.py:173`)가 막는다.
- 1차에는 도구를 직접 부르는 노드가 없으므로 외부 부작용은 에이전트 단계 안에서만 생긴다. 그래서 "실행 중 멈춤 = 결과 불명" 규칙 하나로 덮는다.

### 3.6 권한, 감사, 사용량, 메모리

- **권한(1차):** 소유자만 보고, 고치고, 실행한다(`PRIVATE("workflows")`, Q5). 실행할 때와 단계 시작·재개 때마다 참조 에이전트의 `can_run`을 다시 확인한다.
  OpenFGA `workflow` 타입은 공유를 여는 확장 단계에서 쓴다. 수명 훅(`resource_lifecycle`)에는 지금 등록해 둔다.
- **감사:** `workflow.create`, `workflow.publish`, `workflow.run_start`, `workflow.run_finish`(결과·사유), `workflow.approval_decide`, `workflow.run_cancel`,
  `workflow.step_unknown`. 새 감사 동작마다 내용 단언 시험을 함께 둔다.
- **사용량:** 단계 실행은 `run_source=WORKFLOW`로 귀속하고 `workflow_runs`에 합계를 둔다. 지출 한도는 실행자 기준.
- **메모리:** 새 실행 경로 `workflow`(Q4). `memory_paths`의 경로 표와 ADR-025 표에 행을 더한다.

### 3.7 화면 (편집기 설계, 근거는 부록 A)

**원칙:** 처음 보는 사람도 "위에서 아래로 읽으면 일의 순서"가 보이게 한다. 배치는 사람이 맞추지 않고 그래프 구조에서 계산한다.
모든 편집(사람의 클릭, AI 제안, 버전 되돌리기)은 같은 **편집 명령**(노드 추가, 연결, 설정 변경, 삭제)으로 표현해 되돌리기·비교·검사를 한 곳에서 한다.

| 영역 | 설계 |
|---|---|
| 배치 | 위→아래 자동 정렬(dagre, MIT). 노드 좌표는 저장하지 않고 매번 계산한다(정의 diff와 AI 편집이 단순해짐). "화면 맞춤" 버튼, 미니맵. 좁은 화면은 같은 그래프를 세로 단계 목록으로 보여 주고 편집도 거기서 한다 |
| 노드 카드 | 아이콘, 이름, 한 줄 요약(에이전트 이름, 조건 요약 "긴급 = 예"), 오류 배지, 실행 중이면 상태 배지. 판단 노드는 아래에 "예/아니오" 핸들 2개, 승인 노드는 "승인/거절" 2개로 고정 |
| 노드 추가 | 연결선 가운데와 노드 아래의 "+" → 검색 팝오버(노드 종류 + 내 에이전트 목록). 같은 동작을 Cmd/Ctrl+K 명령 팔레트로도. 사이드바 끌어다 놓기는 두지 않는다 |
| 노드 설정 | 오른쪽 패널, 탭 3개: **설정**(필수 항목 위로, 누락은 빨간 표시) / **시험**(이 노드만 실행) / **마지막 실행**(입력·출력·시간·오류) |
| 값 넣기 | 입력란에서 `{{` 또는 `/`를 치면 선택기: 시작 노드에서 여기까지 올 수 있는 앞 노드의 출력 필드만, 타입 배지와 최근 실행의 실제 값 미리보기와 함께. 고른 값은 칩으로 보이고 저장은 구조화 참조(3.2) |
| 출력 스키마 | 에이전트 노드의 출력은 표 형식 편집기(필드 이름·타입·필수)로 만들고 JSON은 고급 보기로. 시험 실행 결과에서 "이 모양으로 스키마 만들기" |
| 검사 | 편집하는 동안 서버와 같은 규칙(3.3)을 화면에서도 돌려 노드에 배지를 달고, 위쪽 "확인할 것 N개" 목록에 모은다. 0개여야 게시 버튼이 켜진다(서버가 다시 검사) |
| 시험과 실행 표시 | "시험 실행"은 초안으로 돌고 결과는 캔버스 노드 위에 겹쳐 보인다(대기·실행 중·완료·실패·승인 대기·결과 불명). 노드를 누르면 그 단계의 입출력. 에이전트 노드 시험은 실제 모델을 부르므로 예상 비용을 먼저 보여 준다. "이전 실행 값 불러오기"로 앞 노드를 다시 돌리지 않고 한 노드만 시험 |
| 승인 노드 | 설정은 승인자에게 보일 제목·본문 템플릿, 시간 제한(필수), 시간 초과 시 경로. 실행 표시에서 "승인함에서 열기" |
| 저장·버전 | 초안 자동 저장(revision 충돌은 안내 후 새로고침), 되돌리기/다시 하기, 게시 = 버전 스냅숏. 버전 기록에서 두 버전을 캔버스 위 색 diff로 비교(추가 초록, 삭제 빨강, 변경 노랑)하고 되돌리기 |
| AI로 만들고 고치기 (W5) | 오른쪽 패널의 "AI" 탭. 모델은 편집 명령만 낼 수 있다(자유 JSON을 쓰지 않음). 제안은 캔버스에 색 diff로 겹쳐 보이고 **전체 수락/거절**(버전 diff와 같은 렌더러). 큰 변경은 계획을 먼저 보여 주고 확인받는다. 요청이 모호하면 되묻는다. 자격 증명 값과 과거 실행 데이터는 모델에 보내지 않는다. 수락한 제안도 게시 전 검사를 똑같이 거친다 |
| 단축키 | Cmd/Ctrl+K(추가·이동), Cmd/Ctrl+Z / Shift+Z, Delete, Cmd/Ctrl+D(복제), Cmd/Ctrl+Enter(시험 실행), ?(도움말) |
| 시작 | 빈 초안, 업무 템플릿(Q3 업무), AI로 초안 만들기(W5) |

화면 목록: `/workflows`(목록: 이름, 게시 버전, 최근 실행), `/workflows/[id]`(편집기), `/workflows/[id]/runs`·`/runs/[runId]`(실행 목록·상세: 캔버스 겹쳐 보기 +
단계 목록, 당시 버전, 에이전트 단계의 추적 화면 링크), 승인함(출처 `워크플로` 배지, "업무 승인 / 단계 안 도구 승인" 구분). 사이드바는 템플릿 근처(`app-sidebar.tsx`).

**디자인 계층과 다크 모드 (부록 C)**
- 캔버스 엔진은 `@xyflow/react` 12를 그대로 쓴다(Dify, Langflow, Sim, Vercel 워크플로 템플릿이 모두 같은 엔진).
- 보이는 부분은 Vercel AI Elements의 워크플로 부품(Canvas, Node, Edge, Connection, Controls, Panel, Toolbar; Apache-2.0, shadcn/Tailwind, 코드를 저장소에
  복사해 고쳐 쓰는 방식)과 React Flow UI 무료 부품(MIT)에서 시작해 Moldy 디자인 토큰으로 맞춘다. 가져온 파일은 머리 주석에 출처와 라이선스를 남긴다.
- 다크 모드: `ReactFlow`의 `colorMode`를 `next-themes`의 실제 테마에 연결하고, 노드·연결선·배경·미니맵 색은 전부 CSS 변수(디자인 토큰)로만 쓴다.
  밝은·어두운 화면 캡처를 W3a 완료 확인에 넣는다. 기존 비주얼 설정 캔버스(`visual-settings-flow.tsx`)는 `colorMode`를 쓰지 않으므로 같은 연결을 공유 훅으로 만들어 함께 쓴다.

### 3.8 실행 계약 (ADR-024)

| 항목 | 이 기능의 값 |
|---|---|
| 실행 경로 | 새 경로 `workflow`(접수 source, 봉투, 귀속, run_path). 단계 실행은 invoke 모드 |
| 신원과 회사 분리 | 실행자 = 시작한 사용자. 모든 행 RequiredOrgScope, 단계·재개마다 권한 재확인 |
| 회수 시점 | 에이전트 `can_run` 회수·보관은 다음 단계 접수와 재개 때 거부(`AGENT_ARCHIVED`/404) → 실행 실패 |
| 버전 고정 | 실행은 게시 버전에 고정. 에이전트 고정은 Q1 |
| 외부 부작용 | 3.5절. 실행 중 멈춘 단계는 다시 실행하지 않고 결과 불명 |
| 파일 수명 | 1차 파일 입력 없음. 단계 산출물은 내부 대화의 산출물로 저장, 워크플로 삭제 시 함께 정리 |
| 사용량 | `run_source=WORKFLOW`, 실행별 합계, 실행 전·단계 전 지출 한도 |
| 이전과 되돌리기 | i26은 새 테이블·NULL 허용 열만. 되돌리기는 테이블 삭제(워크플로 데이터 소실 경고) |
| 외부 API | 1차 없음(Agent API로 여는 것은 확장 단계, 기존 `/workflows/run` 호환 경로와 이름이 겹치지 않게) |

### 3.9 쓰는 방식 (근거는 부록 B)

다른 제품은 같은 워크플로를 세 가지로 쓴다.
- **실행형:** 입력을 받아 한 번 돌고 결과를 낸다. 실행 폼, 예약, 웹훅·API, 일괄 실행(CSV)으로 시작한다(Dify Workflow, n8n Form·Schedule·Webhook).
- **대화형:** 사용자 메시지마다 흐름 전체가 돌고 대화 기억을 가진다(Dify Chatflow, n8n Chat Trigger, Flowise Chatflow, OpenAI Agent Builder + ChatKit).
- **도구형:** 게시한 워크플로를 에이전트가 채팅 중에 도구로 부른다(Dify "워크플로를 도구로 게시"·MCP 서버, n8n Call Workflow Tool·MCP Server Trigger,
  Copilot Studio agent flow, Langflow MCP 서버).

Moldy에서 채팅의 주체는 에이전트이고 대화 기억·도구·승인도 에이전트가 가진다. 그래서 대화형 흐름을 따로 만들지 않고, 채팅에서 쓰는 길은 도구형으로 연다(Q9).
- **1차:** 실행형만. 워크플로 화면 "실행" → 시작 노드 입력 폼 → 실행 상세(캔버스 겹쳐 보기), 실행 목록, 승인 대기는 승인함.
- **확장 1:** 도구형. 에이전트 설정에서 게시된 워크플로를 도구로 붙인다. 시작 노드 입력이 도구 인자, 출력 노드가 도구 결과다. 워크플로 실행은 그 채팅 실행의
  하위 실행으로 기록하고, 업무 승인 노드는 채팅의 승인 카드로 뜬다. 실행자는 채팅한 사용자, 권한은 워크플로 `can_run`.
  Copilot Studio(100초 안에 응답)·Agentforce(화면 없는 흐름만)는 도구용 흐름을 짧은 동기 실행으로 제한한다. 우리 워크플로는 승인으로 오래 멈출 수 있으므로,
  도구 호출은 채팅 실행을 표준 인터럽트로 멈춰 두고 워크플로가 끝나면 그 결과로 재개한다(승인함 대기와 같은 방식). 도구 입력·출력 스키마는 게시 버전에 고정한다.
- **확장 2:** 예약(기존 예약 트리거가 에이전트 대신 워크플로를 대상으로), Agent API·웹훅, 일괄 실행.

## 4. 결정 질문

2026-11-08 확정: Q1~Q9 모두 아래 "추천" 열대로. Q6은 디자인 계층(부록 C)을 더해 확정했다.

| 번호 | 결정할 내용 | 선택지 | 추천 |
|---|---|---|---|
| Q1 | 에이전트 노드 버전 고정 방식 | A) 불변 `AgentRevision`을 새로 두고 실행이 리비전에서 설정을 만든다. B) 게시 때 노드별 설정을 워크플로 버전에 복사해 그 복사본으로 실행한다. C) 게시 때 에이전트 설정 지문(`build_agent_spec_payload`의 해시)만 고정하고, 실행은 살아 있는 에이전트로 하되 지문이 다르면 실행을 막고 "다시 게시"를 요구한다 | **C(1차)**. 다른 설정으로 도는 일은 막으면서 실행 경로는 기존 그대로다. A·B는 설정 조립기를 하나 더 만드는 큰 공통 작업이라, 진행 중 실행에도 이전 설정을 써야 하는 요구가 생길 때 A로 간다 |
| Q2 | 1차 판단 방식 범위(기획서 D34와 22.9가 어긋남) | 규칙만 / 규칙+LLM / 규칙+LLM+판단 모델 | **규칙만**. 22.9와 순번표대로. 판단 기록 형식은 22.5를 그대로 써서 LLM·판단 모델 방식이 같은 자리에 붙게 한다. D34는 "판단 모델 방식은 22번 뒤"로 고친다 |
| Q3 | 검증할 실제 업무 1개 | 고객 문의 답변 초안 / 주간 보고서 / 다른 업무 | **고객 문의 답변 초안**: 입력(문의) → 분류 에이전트(JSON: 분류, 긴급) → 판단(긴급?) → 긴급이면 담당자 승인 → 답변 초안 에이전트 → 변환 → 최종 출력, 거절은 실패 종료. 핵심 7종을 모두 쓴다 |
| Q4 | 워크플로 단계의 메모리 경로 | 채팅과 같음 / 예약과 같음(에이전트 기억 읽기, 쓰기는 제안) / API와 같음(없음) | **에이전트 기억 읽기만, 쓰기 도구 없음**. 사람이 지켜보지 않는 자동 흐름이라 개인 기억은 읽지 않고, 단계마다 제안이 쌓이지 않게 한다 |
| Q5 | 1차 공유와 승인자 | 소유자만 / 조직 공유(실행만 허용) | **소유자만**, 업무 승인자도 실행자 본인. 공유와 승인자 지정은 확장 단계에서 OpenFGA `workflow` 타입으로 |
| Q6 | 편집기 구현(기획서 D33) | LangConfig 선택 이식 / 기존 `@xyflow/react` 12로 직접 구현 / xyflow 유료 "Workflow Editor" 템플릿(€129~, ELK 기반) 구매 | **1차는 직접 구현**. 노드 7종이고 비주얼 설정이 이미 xyflow 12를 쓴다. LangConfig는 ReactFlow 11 기반이라 이식 비용이 이득보다 크고, 유료 템플릿은 ELK(EPL/GPL 라이선스 검토 필요)와 자체 상태 구조를 들여와 편집 명령 모델(3.7)과 겹친다 |
| Q7 | 배치 방식 | 자동 정렬만(좌표 저장 안 함) / 자유 배치 + 정렬 버튼 | **자동 정렬만**. 위→아래로 읽히는 순서가 보장되고 diff·AI 편집·좁은 화면 목록이 단순해진다. 노드가 수십 개로 늘어 사람이 묶어 보고 싶어지면 그룹(하위 흐름)으로 푼다 |
| Q8 | AI로 만들고 고치기 시점 | 23번 안(W5, 핵심 노드 검증 뒤) / 나머지 1차 노드와 함께 / 하지 않음 | **23번 안의 W5**. 편집 명령 모델과 diff 렌더러는 W3에서 이미 만들므로 AI 제안은 그 위에 얹는다. 핵심 실행이 검증되기 전에는 열지 않는다 |
| Q9 | 쓰는 방식(3.9) | 실행형만 / 실행형 + 대화형(Chatflow) / 실행형 + 도구형 | **1차 실행형, 확장 1에 도구형, 대화형은 만들지 않음**. 채팅의 주체는 에이전트라 대화형을 따로 두면 기억·승인·도구 설정이 두 군데가 된다. 채팅에서 쓰는 요구는 에이전트가 워크플로를 도구로 부르는 것으로 채운다 |

## 5. PR 순서

| PR | 내용 | 완료 확인 |
|---|---|---|
| W0 | 공통 기반: 에이전트 실행에 출력 스키마(`response_format`) 적용과 검증, `workflow` 실행 경로(접수 source·봉투·귀속·run_path·메모리 경로 표) | 스키마 맞음/틀림/거부/잘림 시험, 채팅·예약·API 회귀 0 |
| W1 | i26 테이블, `/api/workflows` CRUD, 초안 저장(revision 충돌), 정의 스키마 검증, 게시 전 검사, 게시(Q1 고정값), 감사 | 검사 규칙마다 거부 시험, 남의 워크플로 404, 라우트 수·OpenAPI 스냅숏 갱신 |
| W2a | 실행 테이블(i28 `workflow_runs`·`workflow_step_runs`), 실행 시작·조회·취소 API, `workflow.run` 작업, 단계 행 기반 실행기, 에이전트 단계(내부 대화·봉투 `workflow`·구조화 출력, 도구 승인은 차단), 규칙 판단·변환·출력·실패, 게시 고정값 확인(바뀐 에이전트 409), 결과 불명, 취소 | 실제 접수를 거친 끝까지 실행, 중간 중단 뒤 재확보가 같은 단계를 다시 부르지 않음, 형식 불일치 실패, 남의 실행 404 |
| W2b | 업무 승인 노드: `approval_requests.kind="business"`(대화·실행 ID 없이 대기 단계를 가리킴, i29), 승인함 결정 compare-and-set 한 번, 승인/거절 갈래 기록과 실행 재개 작업, 만료는 거절 갈래, 실행 취소는 대기 요청 취소 | 승인 → 이어서 완료, 거절·만료 → 거절 갈래, 동시 결정 한 번만, 대기 중 작업 재확보 무변화, 남·다른 조직 404 |
| W2c | 단계 안 도구 승인 대기와 재개: 단계는 `unattended_approval="wait"`로 돌고, 멈추면 단계·실행이 `waiting`, 요청은 `RunOrigin.workflow_*`로 단계를 가리킴(연쇄 대기는 상속). 결정은 **기존 채팅 재개 경로**(편집 도구명 채우기·가린 인자 복원·한 번 결정 그대로)로 재개하고, `tool_waits.adapt_resume`이 재개 실행에 노드 출력 형식과 대기를 다시 싣는다. 재개 실행이 끝나면 `waiters.settle`이 체크포인트의 응답 도구 호출에서 답을 읽어 검증·저장하고 실행을 이어 간다. 결정 없이 끝난 요청(`abandon`)은 단계 실패. 결과 불명 확인 요청은 W4a에서 구현(3.5). **실행 시간 한도는 1차에 따로 두지 않는다:** 멈춰 기다리는 곳마다 이미 기한이 있고(승인 노드 시간 제한 필수, 도구 승인 만료, 결과 불명 확인 7일), 도는 중인 에이전트 단계는 기존 에이전트 실행의 제한과 실행 취소를 따른다 | 대기 → 재개 → 같은 단계가 구조화 결과로 끝남(에이전트 재호출 없음), 형식 불일치·실패 재개, 남은 요청이 있으면 계속 대기 |
| W3a | 편집기: 편집 명령 모델(되돌리기), 자동 정렬 캔버스와 세로 목록, "+" 추가·명령 팔레트, 오른쪽 설정 패널, 값 선택기(구조화 참조), 출력 스키마 편집기, 실시간 검사, 자동 저장, 목록 화면 | 편집 명령 단위 시험(되돌리기·검사), vitest, 접근성·디자인·i18n 게이트 |
| W3b | 시험과 실행: 시험 실행·실행 상세의 캔버스 겹쳐 보기, 노드별 입출력, 이 노드만 시험(이전 실행 값), 승인함 표시, 버전 기록과 색 diff | 실행 상태 7종 표시, 승인 대기 → 승인함 → 재개가 화면에 반영 |
| W4 | **W4a:** 결과 불명 확인 요청(3.5), 에이전트 단계마다 사용 가능 여부 재확인(3.6: 보관, 권한 회수, 승인 대기 중 회수 포함), worker 교체·긴급 중지 시험. **W4b:** 실제 업무(Q3) 끝까지 E2E(키 없는 시험 모델 + 사내 LiteLLM 한 번), 문서(ADR-026 워크플로 실행, CLAUDE.md 규칙). **W4b 구현:** 새 워크플로 만들기에 업무 템플릿 "고객 문의 답변 초안"(Q3, 핵심 7종, 에이전트 2개를 고름)을 두고, E2E 스택이 backend 옆에 job worker를 띄운다. 증거는 6장 "W4b 완료 증거" | 1장 완료 기준 1~6 증거 |
| W5 | AI로 만들고 고치기: 편집 명령만 내는 모델 도구, 계획 먼저 보기, 캔버스 색 diff 수락/거절, 모호하면 되묻기, 자격 증명 비전송 | 제안이 검사를 우회하지 못함, 거절 시 초안 그대로, 사내 LiteLLM으로 업무 3건 생성 평가 |

## 6. 검증 (기획서 32장 워크플로 행)

- **워크플로 복구:** 승인 대기, 단계 실행 중, 단계 저장 직후 각각 worker를 죽이고 다시 띄운다 → 같은 지점부터 재개, 내부 단계 중복 0건, 실행 중 멈춤은 결과 불명.
- **데이터 계약:** A 노드의 특정 필드만 B에 넘기고, 필드 누락·잘못된 타입 출력을 넣는다 → 선택한 필드만 전달, 스키마 오류는 실패 경로.
- **권한:** 다른 사용자로 실행·조회·승인 시도 404, 승인 대기 중 에이전트 실행 권한 회수 → 재개 거부, 보관된 에이전트를 참조한 게시 거부.
- **동시성:** 같은 승인 요청에 결정 두 번 → 한 번만 반영. 같은 실행에 `workflow.run` 작업 두 개 → 하나만 진행.
- **뮤테이션 검사:** 게시 검사 규칙, 결과 불명 규칙, 권한 재확인, 감사 내용 단언에 뮤테이션을 넣어 시험이 빨개지는지 확인한다.
- PG 전체 레인을 로컬에서 돌린다(새 테이블·열 추가 때마다).

### W4b 완료 증거 (2026-11-09, 1장 완료 기준)

| 기준 | 증거 |
|---|---|
| 1. 화면에서 설계·게시·실행, 실행 상세에 단계·경로·버전·승인 대기 | E2E `frontend/e2e/workflow-inquiry.spec.ts`(키 없는 시험 모델 + job worker): 목록의 "고객 문의 답변 초안" 템플릿으로 만들고 에이전트 2개를 고른 뒤, 캔버스에서 승인 기한을 바꾸고 게시(v1에 반영 확인)·시험 실행 → 실행 상세에 승인 대기·v1·"긴급한가 예"·승인 카드 → 승인 → 완료와 최종 출력. 긴급하지 않은 문의는 "아니오" 경로로 승인 없이 완료. 로컬 3회 연속 통과. 사내 LiteLLM(`gpt-5.4-mini`) 한 번: 분류 refund·긴급 → 승인 대기(7초) → 승인 → 답변 초안·변환·출력까지 completed(78초), 감사 6건 |
| 2. 업무 승인·도구 승인·worker 교체 뒤 같은 지점부터, 내부 단계 중복 없음 | `test_workflow_runs.py`: `test_an_approved_request_continues_the_run`, `test_a_second_job_while_waiting_changes_nothing_and_decide_queues_one`, `test_a_worker_dying_after_a_stored_step_resumes_at_the_next_node`, `test_a_step_envelope_only_accepts_its_own_agent`. `test_workflow_tool_waits.py`: `test_the_resumed_step_answers_in_its_schema_and_the_run_continues`, `test_a_valid_resumed_answer_completes_the_run`, `test_a_step_keeps_waiting_while_another_request_is_pending` |
| 3. 보관·권한 회수·중복 콜백·긴급 중지 | `test_an_agent_archived_after_the_start_is_not_run`, `test_an_agent_no_longer_runnable_during_an_approval_wait_is_not_run`, `test_concurrent_decisions_land_once`, `test_settle_leaves_a_step_that_no_longer_waits`, `test_an_emergency_stop_of_the_agent_stops_the_next_agent_step`, 게시 거부 `test_publish_refuses_agents_the_runner_cannot_use`, 바뀐 에이전트 `test_start_checks_inputs_publication_and_changed_agents` |
| 4. 실행 중 worker 종료 → 다시 실행하지 않고 결과 불명 | `test_a_step_cut_off_mid_run_is_unknown_and_never_runs_again`, `test_approving_an_unknown_step_runs_it_again_in_a_new_conversation`, `test_rejecting_or_not_answering_an_unknown_step_fails_the_run`, `test_canceling_an_unknown_run_ends_it_and_its_check`, 화면 `frontend/tests/pages/workflows/run-detail.test.tsx` |
| 5. 생성·게시·실행·승인·취소·실패 감사 | E2E가 `workflow.create`·`workflow.publish`·`workflow.run_start`·`approval.decided`(승인 success / 거절 denied)·`workflow.run_finish`(success / failure)·`workflow.run_cancel`의 대상과 내용을 단언. 백엔드 `test_create_starts_from_a_start_node_and_is_audited`, `test_archive_hides_freezes_and_audits_once`, 결과 불명 `workflow.step_unknown` 내용 단언 |
| 6. 화면만 완성된 상태는 완료 아님 | 위 E2E가 실제 API·DB·job worker·시험 모델을 거친다(목 없음). W4b에서 화면을 연 뒤 승인 대기가 된 실행의 승인 카드가 새로고침 전까지 안 보이던 버그를 E2E로 찾아 고쳤다(`useWorkflowRunApproval`, 폴링 제거 뮤테이션에 단위 시험이 빨개짐) |

## 7. 위험

| 위험 | 대응 |
|---|---|
| 내부 대화가 목록·검색·공유·회상 등 어딘가로 새어 나옴 | 대화 조회 경로 전수 grep, 내부 대화 숨김 시험을 경로별로 |
| 바깥 그래프와 단계 실행의 이중 상태가 어긋남 | 단계 행을 정본으로, 체크포인트는 진행 위치만. 재개 때 단계 행 우선 |
| 승인함 재개 경로가 채팅 실행 경로라 `run_path`가 채팅으로 바뀜 | 재개는 체인의 첫 실행 경로를 따르는 기존 규칙(`origin_run_path`)에 `WORKFLOW` 행 추가, 시험 |
| 범위가 범용 자동화 도구로 커짐(기획서 위험표) | 1차는 7종만, 나머지는 업무 검증 뒤 |
| 구조화 출력이 모델마다 다르게 실패 | W0에서 사내 LiteLLM 두 모델(`gpt-5.4-mini`, `claude-sonnet-5`)로 성공·실패 형태를 기록 |

## 8. 기획서와 다른 점 (반영 요청)

1. **선행 조건 추가:** 22.3은 "JSON 구조화 출력 계약은 워크플로 1차 전에 만든다"고 했지만 순번표 23번 선행 조건은 13번뿐이다. 이 문서는 W0에 넣는다
   (16번 지침 섹션의 출력 형식은 나중에 같은 계약을 쓴다).
2. **D34:** "1차부터 규칙, LLM, 판단 모델 모두 지원"은 22.9·순번표와 어긋난다. Q2 추천대로 고친다.
3. **버전 고정:** 22.7은 AgentRevision 또는 워크플로 버전 안 복제 중 하나를 고르라고 했다. 이 문서는 세 번째 안(지문 고정 + 바뀌면 실행 거부)을 1차로 제안한다(Q1).
4. **편집기:** D33(LangConfig 선택 이식)을 1차에는 직접 구현으로 바꾸자고 제안한다(Q6).

## 부록 A. 편집 화면 리서치 (2026-11-08)

조사 대상: OpenAI Agent Builder(2026-11-30 종료 예정), n8n(AI Workflow Builder 포함), Dify, Langflow, Sim, Zapier, Make(Maia), Microsoft Copilot Studio,
Google Agent Designer, Vellum, Gumloop, LangGraph Studio, React Flow 12 생태계. 공식 문서로 확인한 것만 적고, 확인하지 못한 것은 표시했다.

**이미 표준이 된 것 (우리도 기본으로 둔다)**

| 패턴 | 사례 |
|---|---|
| 캔버스 + 오른쪽 설정 패널 | Copilot Studio([flow designer](https://learn.microsoft.com/en-us/microsoft-copilot-studio/workflows-experience/flow-designer)), Zapier Visual Editor, Dify, OpenAI |
| 자동 정렬 버튼과 화면 맞춤 | Copilot Studio "Tidy up"·세로 배치 전환, Dify Ctrl+O, n8n "tidy up" |
| 노드 뒤 "+"/"Add a step" → 검색형 추가, 명령 팔레트 | Copilot Studio, n8n(N, Cmd+K, [단축키](https://docs.n8n.io/build/keyboard-shortcuts)) |
| 앞 단계 출력을 고르는 변수 선택기 | Dify `/` 삽입([key concepts](https://docs.dify.ai/en/use-dify/getting-started/key-concepts)), n8n 끌어다 놓기로 식 생성([data mapping](https://docs.n8n.io/data/data-mapping/data-mapping-ui/)) |
| 한 노드만 시험, 마지막 실행 보기, 출력 고정 | Dify([step run](https://docs.dify.ai/en/use-dify/debug/step-run.md)), Langflow Freeze([components](https://docs.langflow.org/concepts-components)), n8n pin |
| 실행 결과를 캔버스 노드에 겹쳐 보기 | Copilot Studio Activity 패널 |
| 노드 오류 표시와 게시 차단 목록 | Dify Checklist, Copilot Studio health center |
| 게시 = 버전 스냅숏, 복원 | OpenAI([Agent Builder](https://developers.openai.com/api/docs/guides/agent-builder)), n8n([history](https://docs.n8n.io/build/manage-workflows/view-change-history)) |
| 옆 패널 자연어 생성 채팅 | n8n([AI Workflow Builder](https://docs.n8n.io/build/ways-of-building-workflows/ai-workflow-builder)), Sim, Zapier Copilot, Gumloop |

**차별화 요소 (우리가 고른 것)**
- **AI 변경을 미리 보고 수락/거절:** Sim Copilot은 기준 스냅숏과 제안을 분리한 diff로 보여 주고 수락/거절한다([docs](https://docs.sim.ai/copilot), PR #2086). Copilot Studio는 계획을 먼저 보여 주고 확인받는다([flow-nl](https://learn.microsoft.com/en-au/microsoft-copilot-studio/flow-nl)). Make Maia는 정해진 도구 호출로만 시나리오를 고치고, 모호하면 되묻는다([system card](https://help.make.com/maia-by-make-system-card)). → 3.7 "AI로 만들고 고치기".
- **이전 실행 값으로 한 노드 시험, 선택기에 실제 값 미리보기:** Copilot Studio. → 3.7 "값 넣기", "시험과 실행 표시".
- **버전 간 시각 diff:** n8n에도 없다. AI diff와 같은 렌더러로 만든다.
- 체크포인트에서 갈라 다시 실행(LangGraph Studio)은 2단계로 미룬다.

**자동 정렬 엔진** ([React Flow layouting](https://reactflow.dev/learn/layouting/layouting)): dagre(MIT, 트리·DAG에 단순하고 빠름), d3-hierarchy(ISC, 트리 전용),
elkjs(EPL-2.0 또는 GPL-3.0, 연결선 경로·하위 흐름 지원, 라이선스 검토 필요). → **dagre**. 반복·병렬 그룹이 생기는 확장 단계에서 다시 본다.
xyflow 유료 템플릿("Workflow Editor", "AI Workflow Editor", €129~)은 참고만 한다(Q6).

확인하지 못한 것: OpenAI Agent Builder의 캔버스 방향과 추가 방식, n8n의 연결선 "+" 동작과 AI 변경 단위 되돌리기, Google Agent Designer의 캔버스 유무, Vellum "View Changes" 원문.

## 부록 B. 쓰는 방식 리서치 (2026-11-08)

| 제품 | 실행형 | 대화형 | 도구형 |
|---|---|---|---|
| Dify | Workflow: 한 번 실행, 트리거(예약·웹훅·연동 이벤트)는 Workflow만([workflow-chatflow](https://docs.dify.ai/en/cloud/use-dify/build/workflow-chatflow)). 웹앱 실행 폼과 CSV 일괄 실행([workflow webapp](https://docs.dify.ai/en/use-dify/publish/webapp/workflow-webapp)), API | Chatflow: 메시지마다 실행, Answer 노드로 끝남, 대화 기억 | 워크플로를 도구로 게시해 에이전트·다른 워크플로가 호출([v0.6.9](https://github.com/langgenius/dify/releases/tag/0.6.9)), 앱을 MCP 서버로 게시([publish mcp](https://docs.dify.ai/en/use-dify/publish/publish-mcp)) |
| n8n | Form Trigger(호스팅 폼), Schedule, Webhook, 한 워크플로에 트리거 여러 개 | Chat Trigger: 호스팅 채팅 페이지 또는 사이트 삽입, Agent 노드와 연결 | Call n8n Workflow Tool(에이전트가 하위 워크플로 호출), MCP Server Trigger |
| Copilot Studio | 게시된 에이전트의 자율 트리거(예약·메일) | 대화형 에이전트는 사용자가 시작 | agent flow: "에이전트가 흐름을 호출할 때" 트리거 하나, 동기 실행으로 토픽이 결과를 기다림 |
| OpenAI Agent Builder | 게시 = 버전 스냅숏, Agents SDK 코드 내보내기 | 워크플로 ID를 ChatKit에 넣어 채팅으로 배포([agent builder](https://developers.openai.com/api/docs/guides/agent-builder), 2026-11-30 종료 예정) | - |
| Flowise | Prediction API, 웹훅 | Chatflow·Agentflow V2, 삽입 채팅 위젯([docs](https://docs.flowiseai.com)) | - |
| Langflow | API 실행 | Playground | 프로젝트를 MCP 서버로, 켠 흐름이 도구([mcp-server](https://docs.langflow.org/mcp-server)) |

읽은 것: 대화형을 둔 제품은 대부분 "흐름 자체가 챗봇"인 구조다. 도구형은 Dify·n8n·Copilot Studio·Langflow가 모두 갖고 있어, 에이전트 중심 제품에서
워크플로를 채팅에 붙이는 표준 방식이다. n8n·Copilot Studio 항목은 공식 문서가 아니라 해설 글로 확인했다.

**에이전트 중심 플랫폼 (2026-11-08 추가 조사)**

| 제품 | 워크플로를 어떻게 쓰나 |
|---|---|
| Microsoft Copilot Studio | 에이전트가 주인이고 흐름은 도구. "에이전트가 흐름을 호출할 때" 트리거와 "에이전트에 응답" 동작이 있어야 하고 100초 안에 실시간 응답해야 도구로 붙는다. 에이전트 전체 또는 토픽 하나에 붙인다([flow-agent](https://learn.microsoft.com/en-us/microsoft-copilot-studio/flow-agent), [workflow tools](https://learn.microsoft.com/en-us/training/modules/copilot-studio-structured-automation/5-add-workflow-tools-agents)) |
| Microsoft Agent Framework (오픈소스) | 에이전트 = 모델이 단계를 고름, 워크플로 = 정해진 순서에 에이전트를 단계로 넣음([overview](https://learn.microsoft.com/en-us/agent-framework/overview/)). `workflow.as_agent()`로 워크플로를 채팅 에이전트처럼 쓴다. 바깥 래퍼의 지시·도구·미들웨어는 안쪽 단계로 전달되지 않아 정책은 안쪽 에이전트에 둔다([as-agents](https://learn.microsoft.com/en-us/agent-framework/workflows/as-agents)) |
| Salesforce Agentforce | 두 방향 다. Flow를 에이전트 동작으로 붙이되 화면 없는 자동 흐름만, 입출력은 만든 뒤 못 바꾼다. 반대로 레코드 변경 흐름 안에서 에이전트를 부른다([dev blog](https://developer.salesforce.com/blogs/2025/04/invoke-agentforce-agents-with-apex-and-flow), [해설](https://salesforcetime.com/?p=7452)) |
| Alibaba Model Studio(百炼) | 에이전트 앱·워크플로 앱·에이전트 오케스트레이션 앱이 따로 있고 모두 API로 부른다. 여러 턴 대화는 에이전트 앱과 "대화형 워크플로" 앱만([application calling](https://www.alibabacloud.com/help/en/model-studio/application-calling)). 에이전트가 워크플로를 도구로 쓰는 공식 설명은 찾지 못했고 플러그인·MCP로 연결한다 |
| AgentScope (Alibaba 계열 오픈소스) | 중앙 에이전트가 작업을 나누는 방식과, 순차·팬아웃 파이프라인으로 에이전트를 묶는 방식([orchestration](https://docs.agentscope.io/building-blocks/orchestration)) |
| Qwen-Agent | 도구·계획·기억을 쓰는 에이전트 프레임워크. 워크플로 기능은 확인하지 못했다 |
| Google Gemini Enterprise Agent Designer | 다단계 에이전트 = 메인 에이전트 + 하위 에이전트, Flow 탭에서 순서를 짠다. 사용자는 채팅에서 그 에이전트를 쓴다([agent designer](https://docs.cloud.google.com/gemini/enterprise/docs/agent-designer)) |
| Amazon Bedrock Flows | 흐름 안에 에이전트 노드(별칭 ARN으로 호출), 에이전트가 부족한 값을 사용자에게 물으며 흐름을 멈추는 기능(2025 미리보기)([agent node](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_AgentFlowNodeConfiguration.html)) |
| CrewAI (오픈소스) | Flow가 상태·분기·반복을 갖는 뼈대, Crew(에이전트 팀)는 Flow가 맡긴 작업을 푼다. 규제·다단계 업무는 Flow가 여러 Crew를 묶는 구성을 권한다([evaluating use cases](https://docs.crewai.com/guides/concepts/evaluating-use-cases)) |

읽은 것: 에이전트 중심 플랫폼은 (1) 워크플로를 에이전트 도구로 붙이거나(Copilot Studio, Agentforce), (2) 워크플로가 에이전트를 단계로 묶어
정해진 순서로 돌리거나(Agent Framework, CrewAI, Bedrock, AgentScope), (3) 그 묶음을 다시 채팅 에이전트처럼 쓰게 한다(Agent Framework `as_agent`,
Gemini Agent Designer). 도구로 붙이는 쪽은 모두 짧은 동기 실행으로 제한한다. 순번 23은 (2), 확장 1은 (1)이고, (3)은 확장 1로 같은 효과를 내므로 따로 두지 않는다.

## 부록 C. 캔버스 디자인 오픈소스 (2026-11-08)

| 후보 | 라이선스·활동 | 무엇인가 | 판단 |
|---|---|---|---|
| Vercel AI Elements 워크플로 부품 | Apache-2.0, 별 2.5k | React Flow 위의 shadcn 스타일 Canvas·Node(머리·본문·꼬리)·Edge(실행 중 애니메이션, 조건·오류 경로) 부품. 코드를 복사해 고쳐 쓴다([workflow 예제](https://elements.ai-sdk.dev/examples/workflow)) | **채택**. 스택(Next 16, React 19, shadcn, Tailwind)이 같다 |
| Vercel `workflow-builder-template` | Apache-2.0, 별 1.2k, 2026-11 활동 | Next 16 + React Flow + shadcn + Jotai 워크플로 빌더 앱 전체([repo](https://github.com/vercel-labs/workflow-builder-template)) | 참고 구현. 화면 구성과 다크 모드 처리만 본다 |
| React Flow UI | MIT(무료 부품), 유료 템플릿 별도 | shadcn CLI로 받는 노드·툴바 부품([components](https://reactflow.dev/components)) | 필요한 부품만 |
| FlowGram (ByteDance) | MIT, 별 8.5k, 2026-11 활동 | 워크플로 플랫폼용 프레임워크. 고정 배치(분기·반복 복합 노드)와 자유 배치를 모두 지원([docs](https://flowgram.ai/en/guide/getting-started/introduction)) | 보류. 고정 배치는 우리 자동 정렬과 잘 맞지만 자체 상태·플러그인 구조와 별도 UI 라이브러리를 들여와 편집 명령 모델(3.7)·shadcn과 겹친다. 반복·병렬 그룹이 필요한 확장 단계에서 다시 본다 |
| Sim | Apache-2.0, 별 30k | React Flow 기반 에이전트 워크플로 앱 | AI 편집 diff 화면만 참고(부록 A) |

## 부록 D. 이전 워크플로 기획 HTML에서 가져온 것 (2026-11-08)

기획서 v2에 합쳐지기 전 "몰디 워크플로우 통합안" HTML(노드 목록, 판단 노드, UI 시안)을 다시 검토했다. 이 문서의 설계와 겹치는 것은 빼고, 새로 가져올 것만 적는다.

**화면 (W3a-2, W3b에 반영)**
| 항목 | 내용 | 반영 |
|---|---|---|
| 편집기 머리 | 경로(홈 / 워크플로 / 이름), 버전 칩(초안 · 게시 vN), "기존 에이전트 N종 재사용" 한 줄, 탭 설계 · 시험 실행 · 실행 기록 · 설정, "게시 전 검사"·"시험 실행" 버튼 | W3a-2(탭 뼈대, 시험 실행 진입), W3b(실행 기록) |
| 노드 카드 | 종류 표식(IN · A · IF · ✓ · OUT), 에이전트 이름("기존 ○○ 에이전트"), **출력 계약 한 줄**(출력: facts[], sources[]), 판단은 "review.passed = true · 판단 방식: 규칙", 승인은 대기 시간 | W3a-2 |
| 데이터 계약 보기 | 캔버스 도구줄 토글: 노드에 입출력 필드, 연결선에 넘어가는 값 이름 | W3a-2 |
| 오른쪽 단계 설정 | "사용할 기존 에이전트" 선택, **"이 단계에 전달할 입력"** 목록(각 값이 "시작 입력.topic", "자료 조사.facts" 칩), 출력 형식 요약, "실행 시작 시 설정 고정, 자격 증명은 몰디가 해석" 안내 | W3a-2(값 선택기의 칩 표기를 "노드 이름.필드"로) |
| 판단 노드 설정 | 판단 방식 3단 전환(규칙 · LLM · 판단 모델) — 1차는 규칙만 켜고 나머지는 "22번 이후"로 잠금 표시 | W3a-2 |
| 목록 | 검색창, 카드 하단 "에이전트 N종 · 수동 실행/스케줄", 버전 칩 색(게시=민트, 초안=주황), "처음부터 구성하거나 템플릿으로 시작" 칩(조사 → 작성 → 검증, 문의 분류 → 담당자 연결, 대화로 구성=W5) | W3a-2(검색·메타), W4(업무 템플릿) |
| 실행 상세 | 상단 요약(상태, 실행 ID, 버전, 입력), "5 / 7 단계 완료", 세로 단계 타임라인(아이콘+한 줄 결과), 오른쪽 단계 상세, **실행 화면 안의 승인 카드**(핵심 값 칩, "이 승인은 업무 단계 승인입니다. 에이전트 내부 도구 승인은 따로 표시" 안내, 승인 후 계속 / 거절하고 종료) | W3b |
| 사이드바 | 에이전트 목록 바로 아래 "워크플로" + 개수 배지 | W3a-2 |

**동작 (계획에 추가)**
- **에이전트 설정 화면의 "사용 중인 워크플로"**: Q1(지문 고정) 때문에 게시된 워크플로가 쓰는 에이전트를 고치면 다음 실행이 막힌다. 에이전트 설정 화면에 그 에이전트를 쓰는 워크플로 목록과 "고치면 다시 게시해야 합니다" 안내를 둔다(W3b). 워크플로 노드에서는 "에이전트 설정 열기" 링크로 기존 설정 화면에 간다.
- **경로**: 실행 상세 `/workflows/[id]/runs/[runId]`, 시험 실행 입력 폼 `/workflows/[id]/runs/new`(W3b).
- **판단 노드 확장 설정**(22번 판단 모델 연동 뒤): 판단 질문 1개, 판단 대상 필드, 기준값(보정 세트로 정해 게시 버전에 고정), 불확실 구간(사람 확인 / 안전한 쪽 / 재판단), 오류·시간 초과 재시도와 경로, 긴 입력 조각 판단(하나라도 true면 true), 입력 민감도("사내 한정" 입력에 사외 판단 방식 연결 시 게시 거부). 판단 기록에 모델 버전·기준값·확률을 남기고 원문은 남기지 않는다.
- **실행자 화면**: 공유가 열리면 편집 권한 없는 사람에게는 입력 폼과 실행 버튼만 보인다.
- **첨부 문서 보안 확인 템플릿**: 문서 텍스트 추출 노드와 판단 모델이 생긴 뒤(2차).

**가져오지 않은 것**
- 캔버스 방향: 시안은 왼쪽 → 오른쪽이지만, Q7(자동 정렬, 위 → 아래)을 유지한다. 위에서부터 읽으면 일의 순서이고, 좁은 화면 목록 보기와 버전 diff가 단순하다.
- 단계마다 이름 붙인 입력 바인딩(`input_bindings`): 우리는 구조화 참조가 든 프롬프트 템플릿을 쓴다. 대신 오른쪽 설정에서 템플릿이 쓰는 참조를 "전달할 입력" 목록으로 모아 보여 준다.
- 노드 29종 1차 범위: 순번표대로 핵심 7종을 먼저 검증한다.
