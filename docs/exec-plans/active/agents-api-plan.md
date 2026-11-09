# 순번 21 OpenAI Agents API 호환 상세 기획 (E2, `/openai/v1`)

- 상태: **v2 확정**, 2026-11-10. 4장 결정 Q1~Q17은 추천안으로 확정했다. 검토에서 Q3(세션 설정 고정)과 Q8(누가 승인하나)은 처음 추천안을 고쳤다(4장에 이유).
  v2에서 **개인 키**(3.7, Q13~Q17)를 더했다. 다른 플랫폼(Agentic OS)의 Agents API 호환 기획처럼 "내가 실행할 수 있는 에이전트 목록 → agent_id로 호출"을 지원한다(사용자: "이렇게 해줘").
- 범위: 기능 확장 기획서 v2의 23장(E2)과 17.3절 무인 실행 정책. 이번에 하는 것은 0~1단계와 2단계 중 **도구 승인 확장**이다.
  클라이언트 함수 도구, 웹훅, spend_control(2단계 나머지)과 artifacts·subagents·traces·`/openai/v1/responses`(3단계)는 다음으로 미룬다.
- 선행: 순번 1(실행 신원·준비), 순번 13(승인함, `approval_requests` 한 번만 반영). 둘 다 끝났다. 기존 Agent API 승인 대기(#366)와
  도구 승인 모드(#406)의 판정 함수 `tool_modes.decide` 위에 얹는다.
- 근거: 기획서 v2 23.1~23.4, 17.3. 현재 코드 조사(2장, 2026-11-10 `main` 33a8212c). openai-python CHANGELOG·api.md(2026-11-10 확인).

## 1. 목표와 완료 기준

외부 개발자가 OpenAI 공식 SDK의 `base_url`만 바꿔 Moldy 에이전트를 부를 수 있게 한다. 지원 범위 밖 옵션은 조용히 무시하지 않고 정해진 오류로 돌려준다.

완료 기준
1. `client.beta.agents.sessions.create / retrieve / stream`, `sessions.events.create / stream`, `sessions.turns.retrieve`, `sessions.items.list`,
   `sessions.turns.items.list`가 고정한 SDK 버전의 타입 검증을 통과한다(CI에서 실제 SDK로 호출).
2. 세션 하나에서 이어 묻기, 취소, 승인 2회, 재시작 뒤 재개, 재개 중 취소가 turn 상태·item 순서·usage 합계를 맞게 낸다(기획서 23.4).
3. 진행 중 turn에 보낸 메시지, 인라인 agent, `environment`가 `none`이 아닌 요청, 범위 밖 메서드는 정해진 오류 형식으로 돌아온다.
4. 에이전트를 편집한 뒤 기존 세션의 다음 turn은 `agent_changed`로 실패하고, 새 세션은 새 설정으로 돈다.
5. `approval_policy="client"` 배포에서 승인이 필요한 도구는 `requires_action` + `x_moldy_tool_approval`로 멈추고, 호출 앱의 결정으로 한 번만 재개된다.
   회사가 강제 승인(`approval`)이나 항상 묻기로 정한 도구는 BLOCKED 결과가 된다.
6. 개인 키로 `agents.list()`를 부르면 그 사용자가 실행할 수 있는(`can_run`) 에이전트가 나오고, 그중 하나로 만든 세션은 채팅과 같은 본인 신원으로 돈다.
   권한을 잃은 에이전트는 목록에서 빠지고 다음 turn이 404가 된다.
7. 기존 `/v1/runs`, `/v1/threads`, Dify 형식, `/v1/chat/completions` 시험이 모두 그대로 통과한다.

## 2. 현재 코드 (조사 결과, 파일:줄)

**엔드포인트와 실행**
- 라우터 `backend/app/routers/agent_runtime_api.py`: `GET /v1/health`(183), `GET /v1/agents`(189, 이미 Moldy 형식으로 쓰여 OpenAI 형식으로 바꿀 수 없음),
  `POST /v1/threads`(237), `POST /v1/runs/wait`(397), `GET /v1/runs/{id}`(409), `POST /v1/runs/stream`(627), 스레드 실행(642, 655),
  Dify 형식(671, 729), `POST /v1/chat/completions`(762).
- SSE 변환 `backend/app/agent_api/sse_adapter.py`: `adapt_internal_stream`(27, Moldy `/v1` 이벤트), `adapt_internal_stream_to_openai`(85, chat completions 조각).
  OpenAI Agents 이벤트(`agent.session.*`) 변환기는 이 옆에 새로 둔다.
- 키 범위 `AgentApiScope = invoke | stream | background | read`(`schemas/agent_api.py:11`), 읽기 검사 `run_reads.py:23`.
- 키 `AgentApiKey`(`models/agent_api.py:59`)는 발급자 `user_id`, `scopes`, `allow_all_deployments`, 배포 연결(`AgentApiKeyDeployment`)을 가진다.
  런타임 요청마다 키 소유자의 `agent#can_deploy`를 다시 확인한다(`authz/agent_api_access.py`). **모든 키가 배포에 묶여 있다.**

**저장**
- `AgentApiThread`(`models/agent_api.py:115`): `public_id`, `deployment_id`, `conversation_id`, `external_user`, `metadata`. **설정 스냅숏 열이 없다.**
- `AgentApiRun`(`models/agent_api.py:146`): `conversation_run_id`, `thread_id`, `mode`, `status`, `input`, `output`, `metadata`. turn을 가리키는 열은 없다.
- 재개 실행은 `conversation_runs.parent_run_id`(`models/conversation_run.py:87`)로 이어진다. 대기 입력의 중복 검사는
  `conversation_run_inputs.client_request_id`(`models/conversation_run_input.py:71`, 대화 단위 유일).

**승인**
- 배포 `approval_policy IN ('inbox','blocked')`(`models/agent_api.py:27`, 기본 `blocked`). 실행 방식 선택 `approval_mode`(`agent_api/run_execution.py:72`):
  native wait 호출이고 `inbox`일 때만 기다리고, 나머지는 BLOCKED.
- 대기·정리 `approvals/api_runs.py`(`pending_for` 58, `settle` 98, `abandon` 132). 결정은 `approvals/service.py` `claim`(260)의 compare-and-set.
- `approval_requests.decider_user_id`·`decided_by`는 `users.id` FK(`models/approval_request.py:150-156`). **API 키를 결정자로 남길 자리가 없다.**
- 도구 판정은 `approvals/tool_modes.decide` 한 곳(#406). 회사 `approval`은 강제, `approval_relaxable`은 에이전트가 풀 수 있음, `always_ask_tools`는 항상 묻기.

**실행 신원과 기억**
- 에이전트 `identity_mode`는 `fixed`(소유자 자격 증명) 또는 `per_user`(호출한 사람의 자격 증명)(`agent_runtime/identity.py:14-16`).
  `per_user`는 사람이 시작한 실행(`CHAT`, `WORKFLOW`)에서만 된다(`identity.py:35, 86-93`). 그래서 API 배포는 `fixed` 에이전트만 받는다(`agent_api/service.py:137`).
- 실행 경로별 기억 접근(`agent_runtime/memory_paths.py:31-43`): `api`는 개인 기억·에이전트 기억 읽기와 쓰기 모두 없음.
- 사용자가 실행할 수 있는 에이전트 확인은 `authz/agent_access.visible_agent_ids`(`can_run`, `agent_access.py:148`).

**설정 고정**
- Moldy 실행은 항상 에이전트의 **현재** 설정을 읽는다. 옛 설정으로 돌리는 경로는 없다.
- 워크플로는 게시할 때 설정 지문(`workflows/pins.agent_fingerprints`, `pins.py:90`)을 저장하고, 실행 때 지문이 다르면 `WORKFLOW_AGENT_CHANGED`로 거부한다
  (`workflows/runner.py:240-258`).

**SDK**
- openai-python 최신 3.48.0(2026-11-09). Agents API는 3.13.0(2026-09-10)에 추가. 기획서 작성 시점 버전 3.35.1도 있다.
  SDK에는 이번 범위 밖인 `sessions.list / update / delete`, `turns.list`, `subagents`, `artifacts`, `traces`도 있다.

## 3. 설계

### 3.1 경로와 오류

- 모든 새 경로는 `/openai/v1` 아래에 둔다. 기존 `/v1`은 그대로다. `OpenAI-Beta` 헤더는 받아서 무시한다.
- 오류는 `{"error": {"type", "code", "message", "param"}}` 한 형식으로 통일한다. 이 라우터 전용 예외 처리기를 두어 기존 `/v1` 응답은 바꾸지 않는다.
- 범위 밖 메서드(`agents` 생성·수정·삭제, `sessions.list/update/delete`, `turns.list` 등)는 405 또는 400 `unsupported_*`로 돌려준다.

| 경로 | 동작 | 키 범위 |
|---|---|---|
| `GET /openai/v1/agents` | 배포 키: 키가 허용한 배포. 개인 키: 그 사용자가 실행할 수 있는 에이전트(3.7). 형식 `{object:"list", data:[{id, object:"agent", name, model}]}` | `read` 또는 `invoke` |
| `POST /openai/v1/agents/sessions` | 세션 생성(첫 `input`이 있으면 첫 turn 시작, `stream`이면 SSE) | `invoke` (+`stream`) |
| `GET /openai/v1/agents/sessions/{id}` | 세션 상태 | `read` 또는 `invoke` |
| `POST .../sessions/{id}/events` | 메시지, 취소, 승인 응답 | `invoke` |
| `GET .../sessions/{id}/events` | SSE 구독 | `stream` |
| `GET .../sessions/{id}/turns/{turn}` | turn 하나 | `read` 또는 `invoke` |
| `GET .../sessions/{id}/items`, `.../turns/{turn}/items` | item 목록(커서) | `read` 또는 `invoke` |

### 3.2 세션, turn, item

- **세션** = `AgentApiThread` 한 행. ID 접두어만 `sess_`로 낸다(`thr_`와 같은 테이블). 세션 상태 idle, in_progress, requires_action, failed는
  그 세션의 마지막 turn에서 계산한다.
- **turn** = 외부 입력 하나. 새 테이블 `agent_api_turns`(public_id `turn_…`, `thread_id`, `status`, `reason_code`, `input`, usage 합계, 시각)를 두고,
  `agent_api_runs.turn_id`로 내부 실행 N건(첫 실행과 승인 뒤 재개 실행)을 잇는다.
- **item** = 대화 메시지와 도구 호출을 변환한 것. 사용자·어시스턴트 메시지, 서버 도구는 `function_call`과 결과 쌍, MCP 도구는 `mcp_call`, 추론 요약.
  새로 저장하지 않고 대화 메시지에서 그때그때 만든다.

turn 상태표

| 내부 상태 | turn 상태 |
|---|---|
| 접수, 큐 대기 | `queued` |
| 실행 중, 재개 실행 중 | `in_progress` |
| `interrupted`(승인 대기) | `requires_action` (실패 아님) |
| 마지막 실행 완료 | `completed` |
| 취소 요청 중 | `in_progress`, 끝나면 `cancelled` |
| 실행 실패, stale 복구 | `failed` (`reason_code`) |
| 승인 만료, API 키 회수, 긴급 중지 | `failed` (`approval_expired`, `key_revoked`, `emergency_stop`) |

usage는 그 turn에 연결된 실행 전체를 중복 없이 합친다.

### 3.3 세션 설정 고정

세션을 만들 때 에이전트 설정 지문(`workflows/pins.agent_fingerprints`)을 `agent_api_threads.agent_fingerprint`에 저장한다. turn을 시작할 때마다
현재 지문과 비교해서 다르면 그 turn을 `failed`(`agent_changed`)로 끝내고, 오류 메시지로 새 세션을 만들라고 안내한다.
API 키 회수, 권한 철회, 자격 증명 접근은 지금처럼 실행할 때마다 다시 확인한다. 기존 `thr_` 스레드는 지문이 비어 있어 지금처럼 최신 설정을 따른다.

### 3.4 이벤트 (SSE)

- Moldy 내부 이벤트를 `agent.session.created`, `.in_progress`, `.idle`, `.requires_action`, `.failed`, `agent.session.turn.created`, `.completed`,
  `turn.item.added`, `turn.output_text.delta`로 바꾸는 변환기를 `sse_adapter.py` 옆에 둔다.
- 끊긴 스트림은 다시 보내 주지 않는다(OpenAI와 같음). 클라이언트는 item 목록으로 복구한다. Moldy 확장으로 `Last-Event-ID` 재개를 지원한다
  (채팅 SSE의 이벤트 커서를 그대로 쓴다).
- 이벤트 전송(`POST .../events`)은 idle 상태의 메시지, 승인 응답, 취소만 받는다. 진행 중 turn에 보낸 메시지는 409 `turn_in_progress`다.

### 3.5 도구 승인 확장

- 배포 `approval_policy`에 `client` 값을 추가한다. 소유자가 켜야 하고 기본값은 지금처럼 `blocked`다.
- `client` 배포의 `/openai/v1` 실행에서 승인이 필요한 도구 호출은 표준 인터럽트로 멈춘다. turn은 `requires_action`이 되고,
  `required_actions[]`에 `{type: "x_moldy_tool_approval", id, tool, args, allowed_decisions}`이 들어간다(args는 승인 카드와 같은 마스킹).
- 호출 앱은 `POST .../events`에 `{type: "x_moldy.input.tool_approval_result", action_id, decision: approve|reject|edit, edited_args?}`를 보낸다.
  결정은 `approval_requests` compare-and-set으로 한 번만 반영되고, 같은 결정의 중복 제출에는 같은 결과를 돌려준다. edit은 기존 HITL 경로를 그대로 탄다
  (도구 이름은 서버가 대기 중인 항목의 위치로 채운다).
- **호출 앱이 결정할 수 있는 도구**는 `tool_modes.decide`가 고른다. 회사가 `approval_relaxable`로 연 등급의 도구와 에이전트 자체 설정으로 묻는 도구만
  호출 앱이 결정한다. 회사 강제 승인(`approval`)과 `always_ask_tools`는 사람이 결정해야 하므로 `client` 배포에서도 BLOCKED 결과가 된다.
  회사 `block`은 지금처럼 도구가 없다.
- 결정자 기록: `approval_requests.decided_by_api_key_id`(FK `agent_api_keys.id`, SET NULL)를 추가하고, 감사에 키 ID와 `metadata.user` 라벨을 남긴다.
  `metadata.user`는 라벨일 뿐 사용자 권한이 아니다.
- 기존 `/v1` 경로와 호환 엔드포인트는 `client` 배포에서도 지금처럼 BLOCKED다(확장 이벤트를 쓰는 클라이언트만 기다린다, 17.3).

### 3.6 Idempotency-Key

- 범위 (회사, API 키, 엔드포인트, 키 값), 보존 24시간. 본문 해시가 다르면 409 `idempotency_key_reused`, 같은 요청이 처리 중이면 409
  `idempotency_in_progress`, 끝났으면 저장한 응답을 그대로 돌려준다.
- 세션 생성용 저장소 `agent_api_idempotency`(작은 테이블)와 24시간 정리 작업을 새로 둔다. 이벤트 전송은 `conversation_run_inputs.client_request_id`를 넓혀 쓴다.
- 승인 응답은 (session, turn, action_id, 결정 주체)로 한 번만 소비한다(3.5의 CAS).

### 3.7 개인 키

배포 키(지금 있는 키)와 별도로 **개인 키**를 둔다. 같은 `agent_api_keys` 테이블에 `kind`(`deployment` | `personal`) 열을 더한다.

| | 배포 키 (지금) | 개인 키 (새로) |
|---|---|---|
| 발급 | 배포 소유자가 배포를 골라 발급 | 사용자 본인이 발급(설정 > API 키). 회사 설정이 허용할 때만 |
| `agents.list()` | 키가 허용한 배포 | 그 사용자가 실행할 수 있는 에이전트(`can_run`, 보관·숨김 에이전트 제외) |
| `agent_id` | 배포 `public_id` | 에이전트의 `runtime_name`(`agent_xxxxxxxx`, 이미 공개용 이름) |
| 실행 신원 | 에이전트 소유자로 고정(`fixed`만) | **키 주인 본인.** 채팅과 같다: `fixed` 에이전트는 소유자 자격 증명, `per_user` 에이전트는 본인 자격 증명 |
| 권한 확인 | 키 소유자의 `can_deploy` | 키 주인의 `can_run`을 요청마다 다시 확인 |
| 만료 | 선택 | 필수, 최대 90일(기본 30일) |
| 승인 방식 | 배포의 `approval_policy` | 키의 `approval_policy`(`blocked` 기본, `inbox`, `client`) |

- 실행 경로는 `api`다. 그래서 도구 승인 모드는 에이전트의 `api` 경로 설정을 따르고, 기억은 읽지도 쓰지도 않는다(`memory_paths`의 `api`).
  개인 키에 본인 기억을 열지는 나중 결정으로 둔다(Q17).
- `per_user` 에이전트가 본인 신원으로 돌도록 `AgentRunSource`에 `API`를 더하고, **개인 키 실행만** 사람이 시작한 실행으로 본다
  (키가 한 사람에게 묶여 있으므로). 배포 키 실행은 지금처럼 `CHANNEL`이다.
- 세션 대화는 키 주인 소유로 만들되 채팅 목록에는 보이지 않는다(지금 API 스레드 대화와 같음). 실행 기록과 사용량은 키 주인에게 귀속된다.
- 회사 설정 `personal_api_keys`(허용 여부, 최대 만료일)를 둔다. 키 이름은 회사·개인 테넌트가 같고 기본값만 다르다(회사 기본 꺼짐, 개인 기본 켜짐).
- 긴급 중지, 회원 탈퇴, 조직에서 나감은 그 사람의 개인 키를 모두 정지한다.

## 4. 결정 (확정)

| # | 결정 | 확정안 | 이유 |
|---|---|---|---|
| Q1 | 이번 범위 | 0~1단계와 2단계의 도구 승인 확장. 나머지 2·3단계는 다음 | 승인이 #366에서 남긴 핵심이고 선행 13이 끝남 |
| Q2 | 세션 저장 | `AgentApiThread` 재사용, 접두어 `sess_` | 기획서 23.3 |
| Q3 | 세션 설정 고정 | 지문 저장 → 바뀌면 `agent_changed`로 turn 실패, 새 세션은 새 설정 | **처음 추천(옛 설정으로 계속 실행)을 고침.** 실행은 현재 설정만 읽고 옛 설정을 재현할 수 없다. 워크플로와 같은 방식 |
| Q4 | turn 상태 | 3.2 상태표. 승인 대기는 requires_action, 만료·키 회수·긴급 중지는 failed+이유 코드 | 승인 대기 turn이 실패로 보이는 문제를 막음(23.3) |
| Q5 | 인라인 agent | 이번에는 모두 거부(`invalid_request_error`) | 허용 필드 설정 화면이 따로 필요 |
| Q6 | 진행 중 turn 메시지 | 409 `turn_in_progress` | 현행 steer 의미가 OpenAI와 다름(23.3) |
| Q7 | Idempotency-Key | 3.6 | 현행 대기 입력 중복 검사를 넓혀 씀 |
| Q8 | 누가 승인하나 | 배포 `approval_policy="client"`(소유자가 켬). 호출 앱은 relaxable·에이전트 자체 질문만 결정, 회사 강제·항상 묻기는 BLOCKED. 결정자 API 키 기록 | **처음 추천을 고침.** 호출 앱은 코드로 자동 승인할 수 있어, 회사가 "사람이 승인"으로 정한 도구까지 맡기면 회사 정책을 우회한다 |
| Q9 | 스트림 | `agent.session.*` 변환기, item 복구, `Last-Event-ID` 확장 | 기획서 23.3 |
| Q10 | SDK 버전 | 구현 시점 최신(지금 3.48.0)으로 dev 의존성 고정. 매주 릴리스 노트의 agents 항목 확인 | 베타라 3.35.1 이후 변경이 많을 수 있음(23.4 베타 변경 대응) |
| Q11 | 범위 밖 SDK 메서드 | 405 또는 400 `unsupported_*` | 조용히 무시하지 않음(완료 기준 3) |
| Q12 | PR 나누기 | 이 기획서 → A1(0~1단계 + 개인 키) → A2(승인 확장) | 리뷰 가능한 크기 |
| Q13 | 개인 키 | 배포 키와 별도로 둔다(같은 테이블, `kind` 열). 목록은 `can_run`, 실행은 본인 신원 | 사용자가 Agentic OS 방식("내 권한의 에이전트 목록 → agent_id로 호출")을 원함. Moldy에 `can_run`과 채팅 본인 신원 실행이 이미 있어 재사용 |
| Q14 | 개인 키 만료 | 필수, 최대 90일, 기본 30일 | 사람 권한 전체를 대신하는 키라 수명을 묶음(Agentic OS는 30일 상한) |
| Q15 | 누가 켜나 | 회사 설정 `personal_api_keys`. 회사 기본 꺼짐, 개인 테넌트 기본 켜짐 | 회사 관리자가 노출 범위를 정함. 테넌트 종류로 기능을 나누지 않고 기본값만 다르게 |
| Q16 | 개인 키 승인 | 키마다 `approval_policy`. `client`면 Q8과 같은 규칙: 호출 앱은 relaxable과 에이전트 자체 질문만, 회사 강제·항상 묻기는 BLOCKED | 앱은 코드로 자동 승인할 수 있음. 키 주인이 사람이어도 회사가 "사람이 직접"이라고 정한 도구는 앱에 맡기지 않음 |
| Q17 | 개인 키 기억 | 이번에는 `api` 경로 그대로(기억 없음) | 외부 앱에 개인 기억을 여는 일은 기억 정책(ADR-025) 결정이 따로 필요 |

## 5. 작업 단위

| PR | 내용 | 마이그레이션 |
|---|---|---|
| A1 | `/openai/v1` 라우터, 오류 형식, agents 목록, sessions 생성·조회, turn 테이블, events(메시지·취소), SSE 변환기, items, Idempotency-Key, 설정 지문, **개인 키**(발급 화면, `can_run` 목록, 본인 신원 실행, 회사 설정), 공식 SDK 계약 시험 | i36: `agent_api_turns`, `agent_api_runs.turn_id`, `agent_api_threads.agent_fingerprint`, `agent_api_idempotency`, `agent_api_keys.kind`, 개인 키의 `approval_policy` |
| A2 | `approval_policy="client"`, `x_moldy_tool_approval`·승인 응답 이벤트, 결정자 API 키, 회사 강제 도구 BLOCKED, 승인 만료·키 회수·긴급 중지 turn 마무리, 배포 설정 화면 선택지, 문서 | i37: `approval_policy` 검사 제약에 `client`, `approval_requests.decided_by_api_key_id` |

## 6. 검증

| 시험 | 내용 |
|---|---|
| 공식 SDK | 고정한 openai-python으로 세션 생성, 스트리밍, 이어 묻기, 취소, 목록 조회가 SDK 타입 검증 통과 (CI, scripted 모델) |
| 승인과 재개 | 승인 2회, 재시작 뒤 재개, 재개 중 취소, API 키 회수 뒤 재개, 승인 만료에서 turn 상태·item 순서·usage 합계 |
| 승인 범위 | relaxable 도구는 호출 앱이 결정, 회사 강제·항상 묻기는 BLOCKED, `blocked` 배포는 지금처럼 BLOCKED, `/v1` 경로는 `client`여도 BLOCKED |
| 중복 | 같은 Idempotency-Key 재전송, 본문이 다른 재사용, 동시 중복, 승인 응답 중복 제출 |
| 설정 변경 | 에이전트 편집 뒤 기존 세션 `agent_changed`, 새 세션 정상 |
| 개인 키 | 목록이 `can_run`과 같음(공유받은 에이전트 포함, 보관·숨김 제외), 본인 신원 실행(`per_user` 에이전트는 본인 자격 증명), 권한을 잃은 뒤 404, 만료·회사 설정 꺼짐·긴급 중지에서 401/403, 다른 사람 세션 접근 404 |
| 기존 API | `/v1/runs`, `/v1/threads`, Dify 형식, `/v1/chat/completions` 시험 그대로 통과 |
| 감사 | 호출 앱 결정의 감사 행에 키 ID·결정·라벨이 남는지 내용까지 단언 |
