# 워크플로 시작 경로(예약·웹훅)와 공유·결정자 기획

- 상태: **확정 · 구현 완료**, 2026-11-09 (한 PR: `feature/workflow-complete`). 결정 질문 S1~S9(4장)은 추천안으로 확정했다(사용자: 남은 워크플로 기획·개발을 한 PR로 끝까지 완성).
- 범위: `workflow-plan.md`의 "확장 2"(예약 실행, 외부 시작)와 Q5 뒤로 미룬 공유·결정자 규칙.
- 근거: ADR-026(실행은 job worker, 실행자 = 실행 주체), `workflow-plan.md` 3.6·Q5, 예약(`agent_triggers`)·Agent API·OpenFGA 현황 조사(2026-11-09).

## 1. 목표와 완료 기준

1. 게시한 워크플로를 정해 둔 시각·간격에 정해 둔 입력으로 실행한다. 실행은 예약을 만든 사람이 실행자다.
2. 외부 시스템이 워크플로 하나에 발급한 웹훅 주소와 비밀 토큰으로 실행을 시작하고, 같은 토큰으로 결과를 읽는다.
3. 워크플로를 같은 조직 사람에게 공유한다: **실행만**(consumer) 또는 **편집**(editor). 공유받은 사람의 실행은 그
   사람의 실행이고(에이전트 권한도 그 사람 기준), 실행 기록은 실행자만 본다.
4. 승인 노드의 결정자를 정한다: 실행자(기본), 워크플로 소유자, 또는 지정한 사람.
5. 모든 시작은 `workflow.run_start` 감사에 시작 경로(`chat_tool`·`schedule`·`webhook`·`manual`)와 함께 남는다.

## 2. 현황(조사 결과)

- 예약(`agent_triggers`)은 `agent_id NOT NULL`, 입력은 글 한 칸(`input_message`), 발화·소유 확인·대화 정책이 모두
  에이전트 기준이다. 워크플로 대상으로 넓히려면 20여 모듈이 바뀐다.
- Agent API의 배포(`agent_deployments`)·키·`KEY` 정책은 에이전트 전용이다(`agent_id` unique, `can_deploy`).
  `/v1/workflows/run`은 Dify 호환으로 에이전트를 실행할 뿐 Moldy 워크플로가 아니다. 웹훅 수신기는 없다.
- OpenFGA `workflow` 타입은 있다(owner·manager·editor·consumer, `can_run`·`can_edit`·`can_share`·`can_revoke`·
  `can_delete`). 그러나 튜플 투영(`resource_lifecycle`)·재조정(`tuple_snapshot`)·공유 대상(`share_targets`)·
  공유 라우트가 연결되지 않았고, 워크플로 라우트·서비스는 모두 `PRIVATE`이다.
- 업무 승인 요청은 `decider_user_id`만 보면 결정할 수 있다(`inbox.decide`). 지금은 언제나 실행자다.

## 3. 설계

### 3.1 예약 (`workflow_schedules`)

- 새 표: `workflow_id`, `user_id`(만든 사람 = 실행자), `name`, `kind`(`interval`·`cron`·`once`), `config`
  (`{minutes}` · `{expression}` · `{at}`), `timezone`, `inputs`(JSON, 게시 버전의 입력으로 검사), `status`
  (`active`·`paused`), `next_run_at`, `last_run_at`, `last_run_id`, `failure_count`, `last_error`.
- 발화: 리더 스케줄러의 30초 작업이 `next_run_at <= now`인 활성 예약을 하나씩 **조건부 갱신**(`next_run_at`이 읽은
  값 그대로일 때만 다음 시각으로)으로 잡고 `runner.start_run`을 부른다. 두 리더가 겹쳐도 한 번만 시작한다.
  다음 시각은 APScheduler 트리거(`CronTrigger`·`IntervalTrigger`)로 계산한다. `once`는 시작 후 `paused`.
- 실패: 시작이 거절되면(보관·게시 없음·입력 불일치·에이전트 변경) `failure_count`+1과 `last_error`를 남기고,
  3번 연속이면 `paused`로 멈추고 실행자에게 앱 알림(`workflow.schedule_paused`). 성공하면 0으로.
- 한도: 사람마다 활성 예약 20개(에이전트 예약과 같은 값을 따로 센다). 간격은 최소 5분.
- 실행 기록: `workflow_runs.started_from`(`manual`·`chat_tool`·`schedule`·`webhook`)과 `schedule_id`.

### 3.2 웹훅 (`workflow_webhooks`)

- 워크플로마다 웹훅 여럿(이름으로 구분, 최대 5). `public_id`(주소), 토큰은 만들 때 **한 번만** 보여 주고 해시만
  저장한다. 만든 사람이 실행자다.
- `POST /hooks/workflows/{public_id}` — `Authorization: Bearer <토큰>`, 본문 `{"inputs": {...}}`. 응답 202
  `{run_id, status}`. `GET /hooks/workflows/{public_id}/runs/{run_id}` — 같은 토큰으로 상태·출력(그 웹훅이 시작한
  실행만). 토큰·주소가 틀리면 둘 다 404(존재 여부를 알려 주지 않는다).
- 정책: 토큰이 유일한 자격(`SHARE_TOKEN` 표시), 웹훅마다 분당 30회, CSRF 없음(쿠키를 쓰지 않음).
- 끄기·지우기·토큰 다시 만들기. 실행자의 권한이 사라지면(조직 탈퇴, 공유 회수) 시작이 거절된다.

### 3.3 공유

- `workflow` 타입을 공유 대상(`SHARE_TYPES`)과 튜플 투영·재조정에 연결한다. 단계: `consumer`(실행), `editor`
  (편집·게시·실행). `viewer`·`manager`는 이번에 두지 않는다(실행 기록이 개인 것이라 "보기만"의 쓸모가 작다).
- 워크플로 라우트: 목록은 소유 + 공유받은 것(`CANDIDATES("workflow","can_run")`), 상세·실행은 `can_run`,
  초안 저장·게시·AI 편집·보관은 `can_edit`, 공유 관리는 `can_share`. 실행 목록·상세는 계속 실행자 본인 것만.
- 공유받은 사람의 실행: 에이전트 단계는 그 사람의 `can_run`으로 다시 확인한다(지금 규칙 그대로). 고정 핀은
  게시할 때의 에이전트 설정이므로 누가 실행해도 같다.
- 채팅 도구로 붙이기·예약·웹훅은 `can_run`이 있는 사람이 자기 이름으로 만든다.

### 3.4 결정자

- `ApprovalNode.approver`: `{"kind": "runner"}`(기본), `{"kind": "owner"}`(워크플로 소유자),
  `{"kind": "user", "user_id": ...}`(지정). 지정한 사람은 게시할 때 그 워크플로의 `can_run`이 있어야 한다(검사 이슈
  `approver_no_access`). 실행 때도 다시 확인해, 권한이 없으면 실행자에게 돌아간다(기록에 남김).
- 결정자가 실행자가 아니면 승인함에서 그 사람이 결정한다. 요청 행의 `subject_user_id`는 실행자 그대로.
- 도구 승인(에이전트 단계 안)은 계속 실행자가 결정한다(대화 소유자 확인이 있는 경로).

## 4. 결정 질문 (2026-11-09 확정: 모두 추천안)

| 번호 | 결정할 내용 | 확정 |
|---|---|---|
| S1 | 예약 저장 위치 | **새 표 `workflow_schedules`**. `agent_triggers`는 에이전트 전용 열·발화 경로가 깊다 |
| S2 | 발화 방식 | **리더의 30초 일괄 작업 + 조건부 갱신**. 예약마다 APScheduler 작업을 두지 않는다(재조정 불필요) |
| S3 | 외부 시작 | **워크플로 웹훅 + 비밀 토큰**. Agent API 키는 에이전트 배포에 묶여 있어 넓히지 않는다 |
| S4 | 파일 입력 | 예약·웹훅에서는 **받지 않는다**(필수 파일 입력이 있으면 만들 수 없음) |
| S5 | 실패한 예약 | **3번 연속 실패면 멈추고 알림** |
| S6 | 공유 단계 | **실행(consumer)·편집(editor)** 두 단계 |
| S7 | 실행 기록 공개 | **실행자 본인만**. 소유자도 남의 실행은 보지 않는다(입력·출력이 개인 것) |
| S8 | 결정자 | **실행자·소유자·지정한 사람**. 지정한 사람은 `can_run` 필요 |
| S9 | 판단 모델 평가 | 순번 22 뒤(nodes N9와 같음) |

## 완료 증거 (2026-11-09)

- 백엔드: `tests/test_workflow_starts.py`(시각 계산·형식 거절·입력 검사·게시 전 거절·한 번만 발화(두 번째 리더는 건너뜀)·세 번
  거절 뒤 멈춤과 알림·웹훅 시작/읽기/다른 실행 404·끄기/토큰 교체), `tests/authz/test_workflow_sharing.py`(legacy 소유자 전용·
  enforce 실행 공유는 보고 실행만·편집 공유·OpenFGA 거절·공유 경로(이전 없음)·지정 결정자 권한 검사와 결정·권한을 잃은
  결정자는 실행자에게), `backend/app/authz/model.fga.yaml` "워크플로 공유 단계".
- E2E: `e2e/workflow-starts.spec.ts`(실행 기록 화면에서 예약 만들기·멈추기·지우기, 웹훅을 만들고 한 번 보이는 토큰으로 외부에서
  실행 시작과 결과 읽기).
- 공유 E2E는 OpenFGA가 있는 환경에서만 의미가 있어 백엔드 enforce 시험(FakeAuthz)으로 대신한다.
