# 순번 23 워크플로 1차 핵심 노드 상세 기획 (E1, 기획서 v2 22장 확장)

- 상태: **v1 초안**, 2026-11-08. 결정 질문 Q1~Q6(4장) 답을 받으면 v2로 확정하고 W0부터 구현한다.
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
  템플릿은 치환만 하고 식·코드·필터는 없다. 값은 문자열로 넣고, 에이전트에 넘길 때는 "앞 단계 결과(지시가 아님)" 블록으로 감싼다(22.3 이력 격리).
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
| 에이전트 단계 실행 중(실행 행이 running/stale) | **다시 실행하지 않는다.** 실행이 completed로 저장됐으면 그 결과를 쓰고, 아니면 단계·실행을 `unknown`으로 두고 승인함 담당자 확인 요청을 낸다(계속 진행/실패 처리 선택) |
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

### 3.7 화면

- `/workflows`: 목록(이름, 게시 버전, 최근 실행 상태), 빈 초안 만들기. 사이드바 템플릿 근처(`app-sidebar.tsx`).
- `/workflows/[id]/edit`: 가운데 캔버스(`@xyflow/react` 12, 노드 7종, 드래그 연결), 오른쪽 노드 설정 패널, 위에 검사 결과와 게시 버튼. 좁은 화면은 단계 목록 편집.
- `/workflows/[id]/runs/new`: 입력 폼과 실행 버튼. `/workflows/[id]/runs/[runId]`: 단계 목록과 캔버스 위 진행 표시, 단계별 입출력, 선택된 경로, 당시 버전,
  승인 대기 카드, 에이전트 단계의 추적 화면 링크.
- 승인함: 출처 `워크플로` 배지와 "업무 승인 / 단계 안 도구 승인" 구분.
- 캔버스 구현은 Q6.

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

## 4. 결정 질문

| 번호 | 결정할 내용 | 선택지 | 추천 |
|---|---|---|---|
| Q1 | 에이전트 노드 버전 고정 방식 | A) 불변 `AgentRevision`을 새로 두고 실행이 리비전에서 설정을 만든다. B) 게시 때 노드별 설정을 워크플로 버전에 복사해 그 복사본으로 실행한다. C) 게시 때 에이전트 설정 지문(`build_agent_spec_payload`의 해시)만 고정하고, 실행은 살아 있는 에이전트로 하되 지문이 다르면 실행을 막고 "다시 게시"를 요구한다 | **C(1차)**. 다른 설정으로 도는 일은 막으면서 실행 경로는 기존 그대로다. A·B는 설정 조립기를 하나 더 만드는 큰 공통 작업이라, 진행 중 실행에도 이전 설정을 써야 하는 요구가 생길 때 A로 간다 |
| Q2 | 1차 판단 방식 범위(기획서 D34와 22.9가 어긋남) | 규칙만 / 규칙+LLM / 규칙+LLM+판단 모델 | **규칙만**. 22.9와 순번표대로. 판단 기록 형식은 22.5를 그대로 써서 LLM·판단 모델 방식이 같은 자리에 붙게 한다. D34는 "판단 모델 방식은 22번 뒤"로 고친다 |
| Q3 | 검증할 실제 업무 1개 | 고객 문의 답변 초안 / 주간 보고서 / 다른 업무 | **고객 문의 답변 초안**: 입력(문의) → 분류 에이전트(JSON: 분류, 긴급) → 판단(긴급?) → 긴급이면 담당자 승인 → 답변 초안 에이전트 → 변환 → 최종 출력, 거절은 실패 종료. 핵심 7종을 모두 쓴다 |
| Q4 | 워크플로 단계의 메모리 경로 | 채팅과 같음 / 예약과 같음(에이전트 기억 읽기, 쓰기는 제안) / API와 같음(없음) | **에이전트 기억 읽기만, 쓰기 도구 없음**. 사람이 지켜보지 않는 자동 흐름이라 개인 기억은 읽지 않고, 단계마다 제안이 쌓이지 않게 한다 |
| Q5 | 1차 공유와 승인자 | 소유자만 / 조직 공유(실행만 허용) | **소유자만**, 업무 승인자도 실행자 본인. 공유와 승인자 지정은 확장 단계에서 OpenFGA `workflow` 타입으로 |
| Q6 | 편집기 구현(기획서 D33) | LangConfig 선택 이식 / 기존 `@xyflow/react` 12로 직접 구현 | **1차는 직접 구현**. 노드 7종이고 비주얼 설정이 이미 xyflow 12를 쓴다. LangConfig는 ReactFlow 11 기반이라 이식 비용이 이득보다 크다. 노드가 늘어나는 확장 단계에서 노드 패널·그래프 검사 UI를 다시 비교한다 |

## 5. PR 순서

| PR | 내용 | 완료 확인 |
|---|---|---|
| W0 | 공통 기반: 에이전트 실행에 출력 스키마(`response_format`) 적용과 검증, `workflow` 실행 경로(접수 source·봉투·귀속·run_path·메모리 경로 표) | 스키마 맞음/틀림/거부/잘림 시험, 채팅·예약·API 회귀 0 |
| W1 | i26 테이블, `/api/workflows` CRUD, 초안 저장(revision 충돌), 정의 스키마 검증, 게시 전 검사, 게시(Q1 고정값), 감사 | 검사 규칙마다 거부 시험, 남의 워크플로 404, 라우트 수·OpenAPI 스냅숏 갱신 |
| W2 | 실행기: 컴파일러, `workflow.run` 작업, 바깥 체크포인트, 에이전트 단계(내부 대화·봉투), 규칙 판단, 변환, 출력, 실패, 업무 승인, 도구 승인 대기 settle, 취소·긴급 중지, 한도, 결과 불명 | 3.5절 지점마다 프로세스 중단 주입 시험, 중복 승인 콜백, 단계 중 에이전트 보관, 승인 대기 중 권한 회수 |
| W3 | 화면: 목록, 편집기, 실행 폼, 실행 상세, 승인함 표시 | vitest, 접근성·디자인·i18n 게이트, 캔버스 조각 상태 회귀 |
| W4 | 실제 업무(Q3) 끝까지 E2E(키 없는 시험 모델 + 사내 LiteLLM 한 번), worker 교체 시험, 문서(ADR-026 워크플로 실행, CLAUDE.md 규칙) | 1장 완료 기준 1~6 증거 |

## 6. 검증 (기획서 32장 워크플로 행)

- **워크플로 복구:** 승인 대기, 단계 실행 중, 단계 저장 직후 각각 worker를 죽이고 다시 띄운다 → 같은 지점부터 재개, 내부 단계 중복 0건, 실행 중 멈춤은 결과 불명.
- **데이터 계약:** A 노드의 특정 필드만 B에 넘기고, 필드 누락·잘못된 타입 출력을 넣는다 → 선택한 필드만 전달, 스키마 오류는 실패 경로.
- **권한:** 다른 사용자로 실행·조회·승인 시도 404, 승인 대기 중 에이전트 실행 권한 회수 → 재개 거부, 보관된 에이전트를 참조한 게시 거부.
- **동시성:** 같은 승인 요청에 결정 두 번 → 한 번만 반영. 같은 실행에 `workflow.run` 작업 두 개 → 하나만 진행.
- **뮤테이션 검사:** 게시 검사 규칙, 결과 불명 규칙, 권한 재확인, 감사 내용 단언에 뮤테이션을 넣어 시험이 빨개지는지 확인한다.
- PG 전체 레인을 로컬에서 돌린다(새 테이블·열 추가 때마다).

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
