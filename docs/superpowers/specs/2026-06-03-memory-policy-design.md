# Memory Policy and UX Design

작성일: 2026-06-03
업데이트: 2026-06-04
프로젝트: Moldy
상태: Partially Implemented Draft

## 0. 분석 기준

이 문서는 2026-06-04 현재 워크트리
`/Users/chester/.codex/worktrees/3ed7/natural-mold`의 소스 코드를 기준으로 다시
분석해 갱신했다.

2026-06-04 19시대 구현 pass 이후, 이 문서는 해당 워크트리의 미커밋 변경사항까지
포함한 상태를 기준으로 한다. 단, 지정 문서 파일 자체는
`/Users/chester/dev/ref/natural-mold` checkout에 있다.

적용한 LangChain/Deep Agents 스킬:

- `framework-selection`: Moldy의 장기 작업, 파일, 스킬, 지속 메모리 요구사항은
  LangChain 단일 agent보다 Deep Agents 레이어가 맞다.
- `deep-agents-memory`: 장기 메모리는 `StateBackend`/`StoreBackend`/
  `CompositeBackend`와 `store=` 인스턴스를 명시적으로 설계해야 한다.

확인한 로컬 런타임:

- `deepagents==0.6.1`
- `create_deep_agent()`는 `memory`, `permissions`, `backend`, `store`,
  `subagents`, `checkpointer` 파라미터를 지원한다.
- `deepagents.backends`에는 `FilesystemBackend`, `StateBackend`,
  `StoreBackend`, `CompositeBackend`가 있다.
- `langgraph.store.postgres`에는 `PostgresStore`, `AsyncPostgresStore`가 있다.

## 1. 배경

Moldy는 현재 LangGraph `AsyncPostgresSaver` checkpointer로 conversation/thread
단기 상태를 유지한다. 또 Deep Agents `memory` 옵션에
`/agents/{agent_id}/AGENTS.md`를 전달해 agent 단위 파일 메모리를 읽는 최소
연결이 있다.

다만 현재 구현은 아직 제품 기능으로서의 장기 메모리가 아니다.

- user 단위 장기 메모리가 없다.
- `StoreBackend`/`CompositeBackend`/LangGraph Store 기반 메모리가 아직 없다.
- `build_agent()`는 `store`를 받을 수 있지만 실제 채팅/트리거 callsite는
  `store=`를 전달하지 않는다.
- `AGENTS.md` 디렉토리는 만들지만 파일 자체를 생성하지 않는다.
- 저장은 Deep Agents built-in `write_file`/`edit_file`이 직접
  `/agents/{agent_id}/AGENTS.md`를 수정하는 방식에 의존한다.
- memory proposal, approval, audit, settings, management UI가 없다.
- 같은 conversation에서 메모리 파일이 변경되어도 Deep Agents
  `MemoryMiddleware`가 checkpoint state의 `memory_contents`를 이미 갖고 있으면
  다시 읽지 않을 수 있다.

따라서 메모리 기능을 제품 기능으로 명시화하고, 사용자가 읽기/쓰기/승인 정책과
저장 범위를 제어할 수 있도록 한다.

## 2. 현재 소스코드 상태

| 영역 | 현재 상태 | 근거/의미 |
| --- | --- | --- |
| Thread memory | 구현됨 | `backend/app/agent_runtime/checkpointer.py`가 `AsyncPostgresSaver` singleton을 초기화하고, conversation id를 `thread_id`로 사용한다. |
| User profile context | 일부 구현됨 | `backend/app/routers/conversations.py`의 `_with_user_display_name_context()`가 `display_name`을 system prompt에 주입한다. 이는 프로필 컨텍스트이지 장기 memory 저장소가 아니다. |
| Agent file memory | 최소 구현됨 | `executor.py`가 `memory_sources = ["/agents/{agent_id}/AGENTS.md"]`를 `create_deep_agent()`에 전달한다. |
| User memory | 1차 구현됨 | `memory_records`가 user scope를 저장하고, `/api/memories` 및 설정 UI에서 관리한다. 아직 LangGraph Store-backed memory는 아니다. |
| Store-backed memory | 없음 | `StoreBackend`, `CompositeBackend`, `PostgresStore` 사용처가 없다. |
| Filesystem backend | 구현됨 | `FilesystemBackend(root_dir=backend/data, virtual_mode=True)`를 사용한다. |
| Filesystem permissions | 구현 진행됨 | `build_filesystem_permissions()`가 현재 runtime skill, 현재 conversation output, 자기 agent `AGENTS.md`만 allow하고 `/skills`, `/agents`, `/runtime`, `/conversations` tree를 deny한다. |
| Skills mount | 개선됨 | broad `/skills/`가 아니라 `/runtime/{thread_id}/skills/` per-thread copy를 사용한다. |
| Memory write approval | 1차 구현됨 | `propose_memory`, `save_user_memory`, `save_agent_memory`가 effective policy를 적용하고 ask 모드에서 `memory_proposals`를 만든다. 승인/거절/수정 후 승인 API와 카드 UI가 있다. |
| Trigger mode | 정책 경계 구현 | memory tool은 `is_trigger_mode`에서 `trigger_memory_write_policy`를 사용한다. trigger write 기본값은 off이며, 별도 trigger E2E는 후속 검증 항목이다. |
| SSE trace | 구현됨 | memory tool 결과를 `memory_proposed`, `memory_saved`, `memory_rejected`, `memory_deleted` SSE event로 변환한다. |
| Memory API/UI | 1차 구현됨 | `/api/memories`, `/api/me/memory-settings`, `/api/agents/{agent_id}/memory-settings`, `/api/memory-proposals/*`와 설정/채팅 카드 UI가 있다. |
| Sub-agent runtime | 별도 이슈 | DB/UI와 `build_agent(subagents=...)` forwarding은 있으나, 현재 `_resolve_agent_context()`와 trigger 경로가 `subagents_config`를 채우지 않는다. memory 설계와 직접 범위는 다르지만 runtime 상태 판단 시 주의한다. |

### 2.1 2026-06-04 구현 반영 요약

이번 구현 pass는 LangChain/Deep Agents 권장 구조 중 Store-backed runtime을 바로
도입하지 않고, 제품 정책과 UX를 먼저 DB-backed record/proposal 모델로 세웠다.
이 선택은 현재 Moldy의 Router -> Service -> Model 패턴, 멀티유저 ownership,
CSRF/JWT 인증, SSE 이벤트 구조와 가장 잘 맞는다.

구현된 1차 범위:

- DB 모델/마이그레이션: `user_memory_settings`, `agent_memory_settings`,
  `memory_records`, `memory_proposals`
- API: user/agent memory settings, memory CRUD, proposal create/get/approve/
  edit-and-approve/reject
- Runtime: memory prompt injection, policy-bound memory tools, explicit memory tool
  instruction prompt, trigger write policy gate
- Streaming: memory tool result -> dedicated memory SSE event
- UI: Settings > Memory 페이지, agent settings memory override, chat memory proposal/
  saved/rejected card, approve/reject/edit-and-approve actions
- UX hardening: 처리된 proposal을 재진입 시 서버 status로 복원하고,
  `addResult` 미지원 런타임에서도 성공 액션이 실패 토스트로 오해되지 않도록 처리

아직 남은 권장 구조:

- `StoreBackend`/`CompositeBackend`/`AsyncPostgresStore` 기반 장기 memory route
- DB record를 Store markdown view로 materialize하는 동기화 계층
- 기존 `/agents/{agent_id}/AGENTS.md` legacy memory migration
- trigger memory write E2E와 proposal 만료/cleanup

## 3. 목표

1. 사용자가 메모리 기능을 켜고 끌 수 있어야 한다.
2. 사용자는 장기 메모리 저장 시 승인 여부를 선택할 수 있어야 한다.
3. 기본 정책은 안전하게 `읽기 켬 + 저장 전 확인`으로 둔다.
4. 계정 전체 기본값을 두고, 에이전트별로 override할 수 있어야 한다.
5. 메모리가 저장되거나 제안되면 채팅 UI에서 명확하게 보여줘야 한다.
6. user memory와 agent memory를 구분해야 한다.
7. 장기 메모리는 Deep Agents 권장 구조에 맞게 `StoreBackend` 또는
   DB-backed Store로 이동한다.
8. trigger/schedule 실행에서는 대화형 승인 부재를 고려한 별도 write policy를 둔다.

## 4. 비목표

1차 범위에서는 다음을 제외한다.

- 벡터 검색 기반 semantic memory
- 자동 memory consolidation/background summarizer
- 조직/팀 단위 shared memory
- 다른 사용자와 memory 공유
- 메모리 기반 추천/개인화 대시보드
- sub-agent runtime wiring 자체의 해결

이 항목들은 Store 기반 memory foundation이 안정된 뒤 후속 기능으로 다룬다.

## 5. 메모리 구분

Moldy에서는 메모리를 수명과 접근 범위로 구분한다.

| 이름 | 범위 | 수명 | 예시 | 현재/목표 저장 방식 |
| --- | --- | --- | --- | --- |
| Thread memory | conversation 하나 | 해당 thread | 이번 대화에서 분석 중인 임시 맥락 | 현재: LangGraph checkpointer |
| User profile context | 사용자 프로필 | 장기 | display name, avatar 설정 | 현재: `users` columns. memory와 분리 |
| User memory | 사용자 전체 | 장기 | "내 이름은 이상윤", "한국어 답변 선호" | 목표: LangGraph Store + DB metadata |
| Agent memory | 특정 agent | 장기 | "이 리서치 에이전트는 표부터 작성" | 현재: `/agents/{agent_id}/AGENTS.md`; 목표: Store + DB metadata |

단기/장기의 기준은 "conversation/thread를 넘어 유지되는가"이다.
user/agent의 기준은 "누가 이 memory를 읽을 수 있는가"이다.

## 6. 정책 모델

### 6.1 정책 우선순위

메모리 정책은 다음 순서로 결정한다.

```text
system default
  -> user default
    -> agent override
      -> run mode 제한(chat | trigger)
```

agent override는 user default보다 넓은 권한을 줄 수 없다. 예를 들어 사용자가
memory write를 `off`로 설정하면 agent가 `auto`로 저장할 수 없다.

### 6.2 시스템 기본값

권장 기본값:

```text
memory_read_enabled = true
memory_write_policy = ask
allowed_scopes = both
trigger_memory_write_policy = off
```

채팅에서는 저장된 메모리를 읽되 새 장기 메모리 저장은 사용자에게 확인한다.
트리거는 대화형 승인자가 없으므로 기본 write를 끈다.

### 6.3 사용자 기본 설정

계정 설정에 다음 옵션을 둔다.

```text
memory_enabled: boolean
memory_read_enabled: boolean
memory_write_policy: off | ask | auto
allowed_scopes: user | agent | both
trigger_memory_write_policy: off | auto
```

의미:

- `off`: 새 메모리 저장 불가
- `ask`: 저장 전 사용자 승인 필요
- `auto`: 사용자 승인 없이 저장하되 저장 완료 UI 표시

`trigger_memory_write_policy`는 의도적으로 `ask`를 두지 않는다. schedule run에는
즉시 응답할 사용자가 없기 때문이다. 후속으로 async approval inbox를 만들면
`propose` 상태를 추가할 수 있다.

### 6.4 에이전트별 override

에이전트 설정에는 다음 옵션을 둔다.

```text
memory_policy_override: inherit | off | ask | auto
memory_scopes_override: inherit | agent_only | user_and_agent
trigger_memory_policy_override: inherit | off | auto
```

예시:

- 일반 업무 에이전트: 계정 기본값 상속
- 개인 비서 에이전트: 채팅에서 자동 저장
- 실험용 에이전트: 메모리 끔
- 외부 mutation tool이 많은 에이전트: 저장 전 확인
- 스케줄 에이전트: trigger write off

## 7. 저장 범위 판단

LLM이 1차로 memory scope를 제안하되, 서버가 정책과 권한을 검증한다.

권장 분류:

| 입력 | 권장 scope |
| --- | --- |
| "내 이름은 이상윤이야" | user |
| "나는 한국어로 짧게 답하는 걸 선호해" | user |
| "이 리서치 에이전트는 검색 결과를 표로 먼저 정리해" | agent |
| "이번 대화에서는 A 파일만 보면 돼" | thread, 장기 저장하지 않음 |

중요한 원칙:

- LangChain/Deep Agents가 user memory인지 agent memory인지 자동으로 완벽히 판단하지 않는다.
- Moldy가 tool schema, prompt, server validation으로 scope를 명시해야 한다.
- LLM이 `user_id`, `agent_id`, namespace를 직접 고르게 하면 안 된다.
- ask 모드에서는 승인 카드에 scope를 표시하고 사용자가 수정할 수 있어야 한다.

## 8. 런타임 구조

### 8.1 현재 구조

```text
AsyncPostgresSaver
  -> conversation/thread state

FilesystemBackend(root_dir=backend/data, virtual_mode=True)
  /runtime/{thread_id}/skills/
    -> 현재 agent에 연결된 skill copy
  /conversations/{thread_id}/
    -> runtime output
  /agents/{agent_id}/AGENTS.md
    -> 현재 agent file memory

FilesystemPermission
  allow read: selected /runtime/{thread_id}/skills/{slug}
  allow read/write: /conversations/{thread_id}
  allow read/write: /agents/{agent_id}/AGENTS.md
  deny read/write: /skills, /agents, /runtime, /conversations protected trees
```

현재 구조의 장점은 파일 권한 격리가 이미 들어왔다는 점이다. 단점은 장기 메모리가
여전히 파일 하나이고, Store namespace, audit, approval, user memory가 없다는 점이다.

### 8.2 목표 구조

```text
AsyncPostgresSaver
  -> thread/conversation state

DB-backed LangGraph Store
  -> long-term user/agent memory files

CompositeBackend
  default: StateBackend
    -> 임시 작업 파일

  /memories/user/
    -> StoreBackend namespace=("users", user_id, "memory")

  /memories/agent/
    -> StoreBackend namespace=("users", user_id, "agents", agent_id, "memory")

  /runtime/{thread_id}/skills/
    -> FilesystemBackend 또는 기존 materialized runtime route

  /conversations/{thread_id}/
    -> FilesystemBackend 또는 artifact storage route
```

장기 메모리는 `AGENTS.md` 파일을 직접 data directory에 저장하는 방식에서
LangGraph Store/PostgresStore 기반으로 이동한다.

### 8.3 Deep Agents integration

`StoreBackend`는 Store 인스턴스가 필요하다. 따라서 app lifespan에서
DB-backed Store singleton을 초기화하고, agent build 시 `store=`와
`CompositeBackend`를 함께 전달한다.

예시 구조:

```python
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend


def build_memory_backend(*, user_id: str, agent_id: str, thread_id: str):
    return lambda runtime: CompositeBackend(
        default=StateBackend(runtime),
        routes={
            "/memories/user/": StoreBackend(
                store=postgres_store,
                namespace=lambda _rt: ("users", user_id, "memory"),
            ),
            "/memories/agent/": StoreBackend(
                store=postgres_store,
                namespace=lambda _rt: ("users", user_id, "agents", agent_id, "memory"),
            ),
        },
        artifacts_root=f"/conversations/{thread_id}/",
    )
```

`create_deep_agent()`에는 다음을 전달한다.

```python
create_deep_agent(
    ...,
    backend=build_memory_backend(...),
    store=postgres_store,
    memory=[
        "/memories/user/profile.md",
        "/memories/agent/AGENTS.md",
    ],
)
```

단, memory 저장은 LLM의 raw `edit_file` 호출에만 맡기지 않는다. 앱이 추적 가능한
전용 memory tool을 제공한다.

### 8.4 MemoryMiddleware reload 주의

Deep Agents `MemoryMiddleware`는 state에 `memory_contents`가 이미 있으면 source를
다시 읽지 않는다. 같은 conversation에서 memory save/delete가 발생한 뒤 바로 다음
turn이 최신 memory를 보려면 다음 중 하나가 필요하다.

- memory 저장/삭제 시 현재 thread checkpoint의 `memory_contents`를 invalidate한다.
- memory content에 version key를 두고 middleware state를 갱신한다.
- Deep Agents `memory` 옵션 대신 Moldy custom middleware가 매 turn DB/Store에서
  fresh memory view를 읽어 system prompt에 주입한다.

1차 구현에서는 "저장 승인 후 다음 새 conversation부터 반영"으로 제한하지 말고,
동일 conversation 다음 turn 반영까지 테스트하는 것을 권장한다.

## 9. Memory Tools

### 9.1 전용 tool

다음 tool을 추가한다.

```text
propose_memory
save_user_memory
save_agent_memory
list_memories
delete_memory
```

1차 구현에서는 `propose_memory`, `save_user_memory`, `save_agent_memory`를 우선한다.

### 9.2 Tool 호출 정책

LLM은 기억할 가치가 있는 정보를 발견하면 다음 중 하나를 호출한다.

```text
propose_memory(scope, content, reason)
save_user_memory(content, reason)
save_agent_memory(content, reason)
```

서버는 effective policy를 계산한다.

```text
off:
  저장하지 않고 tool result로 거부 사유 반환

ask:
  즉시 저장하지 않고 memory_proposed 이벤트 생성

auto:
  즉시 저장하고 memory_saved 이벤트 생성
```

`save_*` tool이 호출되어도 서버 정책이 `ask`이면 저장하지 않고 proposal로 degrade한다.
LLM이 tool 이름으로 정책을 우회할 수 없어야 한다.

### 9.3 SSE 이벤트 연결

현재 `streaming.py`는 tool call/result를 SSE로 emit하고, `message_events`에 partial
flush/finalize한다. memory tool 결과도 이 경로를 활용한다.

권장 구현:

1. memory tool이 DB에 `memory_proposals` 또는 `memory_records` row를 쓴다.
2. tool result는 structured JSON 문자열 또는 typed payload를 반환한다.
3. `streaming.py`가 memory tool의 result를 감지해 `memory_proposed`,
   `memory_saved`, `memory_rejected` 같은 dedicated SSE event를 추가 emit한다.
4. 기존 `tool_call_result`는 디버그/trace용으로 유지하거나 UI에서 숨긴다.

이렇게 하면 live stream, resume replay, share/debug trace가 모두 같은
`message_events` 기반을 재사용할 수 있다.

## 10. UI/UX

### 10.1 채팅 UI: 저장 전 확인

`ask` 모드에서는 채팅 타임라인에 승인 카드를 표시한다.

```text
이 내용을 사용자 메모리에 저장할까요?
"사용자의 이름은 이상윤"

[저장] [수정] [취소]
```

agent memory인 경우:

```text
이 내용을 이 에이전트 메모리에 저장할까요?
"이 에이전트는 보고서 작성 시 표를 먼저 만든다"

[저장] [수정] [취소]
```

승인 카드에는 scope, reason, source conversation을 표시한다. 사용자는 저장 전
scope와 content를 수정할 수 있어야 한다.

### 10.2 채팅 UI: 자동 저장

`auto` 모드에서는 저장 후 작은 system card 또는 toast를 표시한다.

```text
메모리에 저장되었습니다
"사용자의 이름은 이상윤"
```

### 10.3 설정 UI

계정 설정 > 메모리:

```text
메모리 사용
저장된 메모리 읽기
새 메모리 저장 방식: 저장 안 함 / 저장 전 확인 / 자동 저장
저장 가능 범위: 사용자 메모리 / 에이전트 메모리 / 둘 다
스케줄 실행 중 저장: 저장 안 함 / 자동 저장
```

에이전트 설정 > 메모리:

```text
계정 기본값 사용
이 에이전트만 메모리 끄기
이 에이전트는 저장 전 확인
이 에이전트는 자동 저장
저장 범위 제한: 에이전트 메모리만 / 사용자+에이전트 메모리
스케줄 실행 중 저장 정책
```

메모리 관리 화면:

```text
사용자 메모리 목록
에이전트별 메모리 목록
검색
수정
삭제
```

1차 범위에서는 검색은 단순 텍스트 필터로 충분하다.

## 11. API 설계

### 11.1 User memory settings

```text
GET /api/me/memory-settings
PATCH /api/me/memory-settings
```

### 11.2 Agent memory settings

```text
GET /api/agents/{agent_id}/memory-settings
PATCH /api/agents/{agent_id}/memory-settings
```

### 11.3 Memory CRUD

```text
GET /api/memories?scope=user
GET /api/agents/{agent_id}/memories
PATCH /api/memories/{memory_id}
DELETE /api/memories/{memory_id}
```

모든 endpoint는 `get_current_user`와 ownership guard를 통과해야 한다.
없음과 권한 없음은 기존 프로젝트 규칙처럼 외부 응답을 통일한다.

### 11.4 Memory proposal action

```text
POST /api/memory-proposals/{proposal_id}/approve
POST /api/memory-proposals/{proposal_id}/reject
POST /api/memory-proposals/{proposal_id}/edit-and-approve
```

쓰기 endpoint는 ADR-016과 동일하게 CSRF 검증을 적용한다.

## 12. SSE Events

채팅 스트림에 다음 이벤트를 추가한다.

```text
memory_proposed
memory_saved
memory_rejected
memory_deleted
```

예시 payload:

```json
{
  "id": "proposal-id",
  "scope": "user",
  "content": "사용자의 이름은 이상윤",
  "reason": "사용자가 명시적으로 기억해달라고 요청함",
  "policy": "ask",
  "conversation_id": "conversation-id",
  "agent_id": "agent-id"
}
```

`backend/app/agent_runtime/event_names.py`에 상수를 추가하고, frontend SSE 타입에도
같은 이름을 추가한다.

## 13. 데이터 모델

### 13.1 user_memory_settings

사용자 기본 설정은 별도 테이블을 권장한다. M55의 `users.display_name`/avatar
columns와 memory policy는 성격이 다르다.

```text
user_memory_settings
  user_id pk/fk users.id
  memory_enabled
  memory_read_enabled
  memory_write_policy: off | ask | auto
  allowed_scopes: user | agent | both
  trigger_memory_write_policy: off | auto
  created_at
  updated_at
```

### 13.2 agent_memory_settings

```text
agent_memory_settings
  agent_id pk/fk agents.id
  memory_policy_override: inherit | off | ask | auto
  memory_scopes_override: inherit | agent_only | user_and_agent
  trigger_memory_policy_override: inherit | off | auto
  created_at
  updated_at
```

### 13.3 memory_records

StoreBackend만 쓰면 목록/삭제 UI와 감사 로그가 약해질 수 있다. 따라서 UI용
metadata row를 별도로 둔다.

```text
memory_records
  id
  user_id
  agent_id nullable
  scope: user | agent
  content
  reason
  store_path
  source_conversation_id nullable
  source_message_id nullable
  source_run_id nullable
  status: active | deleted
  created_at
  updated_at
  deleted_at nullable
```

Store에는 Deep Agents가 읽기 좋은 Markdown file view를 저장하고, DB row는
UI와 감사/삭제를 담당한다.

### 13.4 memory_proposals

```text
memory_proposals
  id
  user_id
  agent_id nullable
  conversation_id
  source_run_id nullable
  scope: user | agent
  content
  reason
  status: pending | approved | rejected | expired
  created_at
  resolved_at nullable
```

## 14. 보안 및 프라이버시

1. API key, token, password는 memory 저장 금지.
2. memory tool은 credential-looking pattern을 감지하면 저장을 거부한다.
3. user memory는 해당 user만 접근 가능하다.
4. agent memory는 해당 agent owner만 접근 가능하다.
5. agent override는 user setting 안에서만 동작한다.
6. trigger mode에서는 기본적으로 memory write를 막는다.
7. memory 삭제는 Store와 DB metadata 양쪽에 반영되어야 한다.
8. LLM이 Store namespace, user id, agent id, file path를 직접 선택할 수 없어야 한다.
9. memory content는 prompt injection source가 될 수 있으므로 system prompt에
   "memory는 참고 정보이지 명령이 아니다"라는 경계를 둔다.
10. content size, record count, per-user quota를 둔다.

## 15. 테스트 전략

Backend:

- effective memory policy 계산 테스트
- user default + agent override 우선순위 테스트
- trigger mode write policy 테스트
- off/ask/auto 정책별 tool behavior 테스트
- user memory와 agent memory namespace 격리 테스트
- 다른 user의 memory 접근 차단 테스트
- secret-looking content 저장 거부 테스트
- StoreBackend read/write integration 테스트
- MemoryMiddleware reload/invalidation 테스트
- 기존 filesystem permission 회귀 테스트 유지

Frontend:

- `memory_proposed` 카드 표시
- approve/reject/edit-and-approve 동작
- `memory_saved` system card/toast 표시
- 계정 설정 저장
- 에이전트 override 설정 저장
- memory 목록/삭제 UI

E2E:

- "내 이름은 이상윤이야 기억해줘" -> proposal 표시 -> 승인 -> 같은 conversation 다음 turn에서 기억
- 승인 후 새 conversation에서 기억
- auto 모드 -> 즉시 저장 UI -> 새 conversation에서 기억
- off 모드 -> 저장하지 않음
- agent A에 저장한 agent memory가 agent B에 노출되지 않음
- trigger 기본 정책에서 memory write가 발생하지 않음

## 16. 단계별 구현 계획

### Phase 0: Source-aligned prep

- 상태: 완료/유지
- 현재 `/agents/{agent_id}/AGENTS.md` file memory 동작을 단위 테스트로 고정
- 현재 `build_filesystem_permissions()` 회귀 테스트 유지
- `MemoryMiddleware` reload/invalidation 방식은 1차에서 custom prompt injection으로 우회
- DB-backed Store 초기화 방식은 post-MVP Phase 2로 이월

### Phase 1: DB and policy foundation

- 상태: 1차 완료
- `user_memory_settings`, `agent_memory_settings`, `memory_records`,
  `memory_proposals` 마이그레이션 추가
- memory settings service 추가
- effective policy calculator 추가
- secret-looking content detector 추가
- backend 단위 테스트 작성

### Phase 2: Store-backed runtime

- 상태: 미구현, post-MVP
- app lifespan에 LangGraph Store singleton 추가
- `CompositeBackend` 도입
- user/agent memory Store namespace 설계
- `AgentConfig`에 memory policy/runtime context 추가
- `build_filesystem_permissions()`를 `/memories/*` 기준으로 확장
- trigger mode에서 memory write deny 또는 policy-bound allow 적용

### Phase 3: Memory tools and SSE

- 상태: 1차 완료
- `propose_memory`, `save_user_memory`, `save_agent_memory` tool 추가
- off/ask/auto 정책 적용
- `memory_proposed`, `memory_saved`, `memory_rejected`, `memory_deleted` SSE 이벤트 추가
- proposal approve/reject/edit-and-approve API 추가

### Phase 4: Chat UI

- 상태: 1차 완료
- memory proposal card 추가
- memory saved card/toast 추가
- 승인/수정/취소 액션 연결
- resume/replay 시 memory 이벤트 복원 검증

### Phase 5: Settings and management UI

- 상태: 1차 완료
- 계정 메모리 설정 UI
- 에이전트 메모리 override UI
- memory 목록/수정/삭제 UI

### Phase 6: Migration and cleanup

- 상태: 미구현
- 기존 `/agents/{agent_id}/AGENTS.md` 파일을 Store/DB row로 migration
- migration 동안 legacy file memory를 read-only fallback으로 제한
- agent/user 삭제 시 memory cleanup
- 기존 file-based memory write path 제거 또는 policy-bound로 제한

## 17. 작업량 추정

최소 구현:

```text
DB settings + policy + memory tool + SSE + 기본 Store integration + 테스트
약 1.5-2주
```

제품 품질 구현:

```text
위 항목 + 채팅 UI + 설정 UI + memory 관리 UI + E2E + migration
약 3주
```

권장 1차 릴리즈 범위:

```text
memory read 기본 켬
memory write 기본 ask
trigger write 기본 off
user default + agent override
user memory + agent memory
proposal card
saved card/toast
memory 목록/삭제
```

## 18. 열린 결정 사항

1. memory 저장 content를 Markdown file 형태로 유지할지, record 단위 JSON으로
   만들지 결정해야 한다.
   - 권장: DB는 record 단위, Store에는 Deep Agents가 읽기 좋은 Markdown view를 materialize.
2. 같은 conversation에서 저장된 memory를 즉시 다시 읽게 할 방법을 결정해야 한다.
   - 권장: memory save/delete 후 `memory_contents` state invalidation 테스트를 먼저 작성한다.
3. agent override가 user default보다 강한 권한을 가질 수 있는지 결정해야 한다.
   - 권장: user default가 상한선이다. user가 off면 agent도 저장 불가.
4. memory proposal 만료 시간을 둘지 결정해야 한다.
   - 권장: 24시간 후 expired.
5. 자동 저장 모드에서도 민감정보 감지 시 ask로 degrade할지 결정해야 한다.
   - 권장: 민감정보 의심 시 저장 거부 또는 ask로 degrade.
6. trigger run에서 memory write를 완전히 금지할지, explicit opt-in auto를 허용할지
   결정해야 한다.
   - 권장: 1차는 off, 2차에서 explicit opt-in.

## 19. 최종 권장안

Moldy의 메모리 기능은 다음 원칙으로 구현한다.

```text
기본값은 안전하게:
  채팅은 읽기 켬 + 저장 전 확인
  트리거는 읽기 켬 + 저장 끔

정책은 유연하게:
  사용자 기본값 + 에이전트별 override
  단, 사용자 설정이 상한선

저장은 명시적으로:
  LLM의 raw edit_file이 아니라 memory 전용 tool

표시는 투명하게:
  proposed/saved/rejected/deleted 이벤트를 채팅 UI에 노출

저장소는 권장 구조로:
  short-term은 AsyncPostgresSaver checkpointer
  long-term은 StoreBackend/PostgresStore + DB metadata

격리는 현재 성과를 유지:
  기존 FilesystemPermission 격리를 Store/CompositeBackend 구조에도 계속 적용
```

이 구조는 ChatGPT식 memory UX를 지원하면서도, 멀티유저/멀티에이전트 환경에서
권한과 저장 범위를 명확히 유지한다.
