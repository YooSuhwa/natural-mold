# Memory Policy and UX Design

작성일: 2026-06-03
프로젝트: Moldy
상태: Draft

## 1. 배경

현재 Moldy는 LangGraph checkpointer를 통해 conversation/thread 단기 메모리를 사용하고,
Deep Agents `memory` 옵션으로 `/agents/{agent_id}/AGENTS.md` 파일을 로드하는 agent 단위
장기 메모리의 최소 구조를 갖고 있다.

하지만 현재 구조에는 다음 한계가 있다.

- 장기 메모리가 user 단위가 아니라 agent 단위 파일에 묶여 있다.
- 메모리 저장 여부와 저장 범위를 사용자가 명확히 통제하기 어렵다.
- LLM이 `edit_file`을 호출해야 장기 저장이 일어나므로 앱이 저장 이벤트를 안정적으로 알기 어렵다.
- "메모리에 저장되었습니다" 같은 사용자 피드백 UI를 만들기 어렵다.
- Deep Agents 권장 구조인 StoreBackend/CompositeBackend 기반의 cross-thread memory와 거리가 있다.

따라서 메모리 기능을 제품 기능으로 명시화하고, 사용자가 읽기/쓰기/승인 정책을 제어할 수 있도록 한다.

## 2. 목표

1. 사용자가 메모리 기능을 켜고 끌 수 있어야 한다.
2. 사용자는 장기 메모리 저장 시 승인 여부를 선택할 수 있어야 한다.
3. 기본 정책은 안전하게 `읽기 켬 + 저장 전 확인`으로 둔다.
4. 계정 전체 기본값을 두고, 에이전트별로 override할 수 있어야 한다.
5. 메모리가 저장되거나 제안되면 채팅 UI에서 명확하게 보여줘야 한다.
6. user memory와 agent memory를 구분해야 한다.
7. Deep Agents 권장 방식에 맞게 장기 메모리는 StoreBackend 또는 DB-backed Store로 이동한다.

## 3. 비목표

이번 설계의 1차 범위에서는 다음을 제외한다.

- 벡터 검색 기반 semantic memory
- 자동 memory consolidation/background summarizer
- 조직/팀 단위 shared memory
- 다른 사용자와 memory 공유
- 메모리 기반 추천/개인화 대시보드

이 항목들은 Store 기반 memory foundation이 안정된 뒤 후속 기능으로 다룬다.

## 4. 메모리 구분

Moldy에서는 메모리를 수명과 접근 범위로 구분한다.

| 이름 | 범위 | 수명 | 예시 | 저장 방식 |
| --- | --- | --- | --- | --- |
| Thread memory | conversation 하나 | 해당 thread | 이번 대화에서 분석 중인 임시 맥락 | LangGraph checkpointer |
| User memory | 사용자 전체 | 장기 | "내 이름은 이상윤", "한국어 답변 선호" | LangGraph Store |
| Agent memory | 특정 agent | 장기 | "이 리서치 에이전트는 표부터 작성" | LangGraph Store |

단기/장기의 기준은 "conversation/thread를 넘어 유지되는가"이다.
user/agent의 기준은 "누가 이 memory를 읽을 수 있는가"이다.

## 5. 정책 모델

### 5.1 정책 우선순위

메모리 정책은 다음 순서로 결정한다.

```text
agent override가 있으면 사용
없으면 user default 사용
없으면 system default 사용
```

### 5.2 시스템 기본값

권장 기본값:

```text
memory_read_enabled = true
memory_write_policy = ask
allowed_scopes = both
```

즉, 저장된 메모리는 사용하되 새 장기 메모리 저장은 기본적으로 사용자에게 확인한다.

### 5.3 사용자 기본 설정

계정 설정에 다음 옵션을 둔다.

```text
memory_enabled: boolean
memory_read_enabled: boolean
memory_write_policy: off | ask | auto
allowed_scopes: user | agent | both
```

의미:

- `off`: 새 메모리 저장 불가
- `ask`: 저장 전 사용자 승인 필요
- `auto`: 사용자 승인 없이 저장하되 저장 완료 UI 표시

### 5.4 에이전트별 override

에이전트 설정에는 다음 옵션을 둔다.

```text
memory_policy_override: inherit | off | ask | auto
memory_scopes_override: inherit | agent_only | user_and_agent
```

예시:

- 일반 업무 에이전트: 계정 기본값 상속
- 개인 비서 에이전트: 자동 저장
- 실험용 에이전트: 메모리 끔
- 외부 mutation tool이 많은 에이전트: 저장 전 확인

## 6. 저장 범위 판단

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
- ask 모드에서는 승인 카드에 scope를 표시하고 사용자가 수정할 수 있어야 한다.

## 7. 런타임 구조

### 7.1 현재 구조

```text
AsyncPostgresSaver
  -> conversation 단기 메모리

FilesystemBackend(root_dir=data, virtual_mode=True)
  -> skills
  -> conversation outputs
  -> /agents/{agent_id}/AGENTS.md
```

### 7.2 목표 구조

```text
AsyncPostgresSaver
  -> thread/conversation state

CompositeBackend
  default: StateBackend
    -> 임시 작업 파일

  /memories/user/
    -> StoreBackend namespace=("users", user_id, "memory")

  /memories/agent/
    -> StoreBackend namespace=("users", user_id, "agents", agent_id, "memory")

  /runtime/{thread_id}/skills/
    -> FilesystemBackend 또는 sandboxed route

  /conversations/{thread_id}/
    -> FilesystemBackend 또는 artifact storage route
```

장기 메모리는 `AGENTS.md` 파일을 직접 data directory에 저장하는 방식에서 벗어나
LangGraph Store/PostgresStore 기반으로 이동한다.

### 7.3 Deep Agents integration

`create_deep_agent`에는 다음을 전달한다.

```python
create_deep_agent(
    ...,
    backend=composite_backend,
    store=postgres_store,
    memory=[
        "/memories/user/profile.md",
        "/memories/agent/AGENTS.md",
    ],
)
```

단, memory 저장은 LLM의 raw `edit_file` 호출에만 맡기지 않는다. 앱이 추적 가능한 전용 memory tool을 제공한다.

## 8. Memory Tools

### 8.1 전용 tool

다음 tool을 추가한다.

```text
propose_memory
save_user_memory
save_agent_memory
list_memories
delete_memory
```

1차 구현에서는 `propose_memory`, `save_user_memory`, `save_agent_memory`를 우선한다.

### 8.2 Tool 호출 정책

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

### 8.3 저장 이벤트 추적

메모리 저장은 서버가 `MemoryEvent`로 기록한다.

```text
proposed
saved
rejected
deleted
```

이 기록은 채팅 UI 이벤트, 감사 로그, 메모리 관리 화면의 기반이 된다.

## 9. UI/UX

### 9.1 채팅 UI: 저장 전 확인

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

### 9.2 채팅 UI: 자동 저장

`auto` 모드에서는 저장 후 작은 system card 또는 toast를 표시한다.

```text
메모리에 저장되었습니다
"사용자의 이름은 이상윤"
```

### 9.3 설정 UI

계정 설정 > 메모리:

```text
메모리 사용
저장된 메모리 읽기
새 메모리 저장 방식: 저장 안 함 / 저장 전 확인 / 자동 저장
저장 가능 범위: 사용자 메모리 / 에이전트 메모리 / 둘 다
```

에이전트 설정 > 메모리:

```text
계정 기본값 사용
이 에이전트만 메모리 끄기
이 에이전트는 저장 전 확인
이 에이전트는 자동 저장
저장 범위 제한: 에이전트 메모리만 / 사용자+에이전트 메모리
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

## 10. API 설계

### 10.1 User memory settings

```text
GET /api/me/memory-settings
PATCH /api/me/memory-settings
```

### 10.2 Agent memory settings

```text
GET /api/agents/{agent_id}/memory-settings
PATCH /api/agents/{agent_id}/memory-settings
```

### 10.3 Memory CRUD

```text
GET /api/memories?scope=user
GET /api/agents/{agent_id}/memories
PATCH /api/memories/{memory_id}
DELETE /api/memories/{memory_id}
```

### 10.4 Memory proposal action

```text
POST /api/memory-proposals/{proposal_id}/approve
POST /api/memory-proposals/{proposal_id}/reject
POST /api/memory-proposals/{proposal_id}/edit-and-approve
```

## 11. SSE Events

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
  "policy": "ask"
}
```

## 12. 데이터 모델

### 12.1 memory_settings

사용자 기본 설정은 users 테이블에 JSON으로 넣을 수도 있지만, 별도 테이블을 권장한다.

```text
user_memory_settings
  user_id
  memory_enabled
  memory_read_enabled
  memory_write_policy
  allowed_scopes
  created_at
  updated_at
```

### 12.2 agent_memory_settings

```text
agent_memory_settings
  agent_id
  memory_policy_override
  memory_scopes_override
  created_at
  updated_at
```

### 12.3 memory_records

StoreBackend만 쓰면 목록/삭제 UI와 감사 로그가 약해질 수 있다. 따라서 UI용 metadata row를 별도로 둔다.

```text
memory_records
  id
  user_id
  agent_id nullable
  scope: user | agent
  content
  reason
  source_conversation_id nullable
  source_message_id nullable
  status: active | deleted
  created_at
  updated_at
```

Store에는 실제 Deep Agents memory file view를 저장하고, DB row는 UI와 감사/삭제를 담당한다.

### 12.4 memory_proposals

```text
memory_proposals
  id
  user_id
  agent_id
  conversation_id
  scope
  content
  reason
  status: pending | approved | rejected | expired
  created_at
  resolved_at nullable
```

## 13. 보안 및 프라이버시

1. API key, token, password는 memory 저장 금지.
2. memory tool은 credential-looking pattern을 감지하면 저장을 거부한다.
3. user memory는 해당 user만 접근 가능하다.
4. agent memory는 해당 agent owner만 접근 가능하다.
5. agent override가 user default보다 넓은 권한을 줄 수 있는지 정책을 명확히 해야 한다.
   - 권장: agent override는 user setting 안에서만 동작한다.
   - 예: user가 memory off면 agent가 auto로 켤 수 없다.
6. memory 삭제는 Store와 DB metadata 양쪽에 반영되어야 한다.

## 14. 테스트 전략

Backend:

- effective memory policy 계산 테스트
- user default + agent override 우선순위 테스트
- off/ask/auto 정책별 tool behavior 테스트
- user memory와 agent memory namespace 격리 테스트
- 다른 user의 memory 접근 차단 테스트
- secret-looking content 저장 거부 테스트
- StoreBackend read/write integration 테스트

Frontend:

- memory_proposed 카드 표시
- approve/reject/edit-and-approve 동작
- memory_saved system card/toast 표시
- 계정 설정 저장
- 에이전트 override 설정 저장
- memory 목록/삭제 UI

E2E:

- "내 이름은 이상윤이야 기억해줘" -> proposal 표시 -> 승인 -> 새 conversation에서 기억
- auto 모드 -> 즉시 저장 UI -> 새 conversation에서 기억
- off 모드 -> 저장하지 않음
- agent A에 저장한 agent memory가 agent B에 노출되지 않음

## 15. 단계별 구현 계획

### Phase 1: Memory foundation

- PostgresStore singleton 추가
- CompositeBackend 도입
- user/agent memory namespace 설계
- memory settings effective policy 계산기 추가
- backend 단위 테스트 작성

### Phase 2: Memory tools and SSE

- `propose_memory`, `save_user_memory`, `save_agent_memory` tool 추가
- off/ask/auto 정책 적용
- `memory_proposed`, `memory_saved` SSE 이벤트 추가
- proposal approve/reject API 추가

### Phase 3: Chat UI

- memory proposal card 추가
- memory saved card/toast 추가
- 승인/수정/취소 액션 연결

### Phase 4: Settings and management UI

- 계정 메모리 설정 UI
- 에이전트 메모리 override UI
- memory 목록/수정/삭제 UI

### Phase 5: Migration and cleanup

- 기존 `/agents/{agent_id}/AGENTS.md` 파일을 Store/DB row로 migration
- agent/user 삭제 시 memory cleanup
- 기존 file-based memory fallback 제거 또는 read-only migration path로 제한

## 16. 작업량 추정

최소 구현:

```text
StoreBackend + policy + memory tool + SSE + 기본 테스트
약 1.5주
```

제품 품질 구현:

```text
위 항목 + 채팅 UI + 설정 UI + memory 관리 UI + E2E + migration
약 2-3주
```

권장 1차 릴리즈 범위:

```text
memory read 기본 켬
memory write 기본 ask
user default + agent override
user memory + agent memory
proposal card
saved card/toast
memory 목록/삭제
```

## 17. 열린 결정 사항

1. memory 저장 content를 Markdown file 형태로 유지할지, record 단위 JSON으로 만들지 결정해야 한다.
   - 권장: DB는 record 단위, Store에는 Deep Agents가 읽기 좋은 Markdown view를 materialize.
2. agent override가 user default보다 강한 권한을 가질 수 있는지 결정해야 한다.
   - 권장: user default가 상한선이다. user가 off면 agent도 저장 불가.
3. memory proposal 만료 시간을 둘지 결정해야 한다.
   - 권장: 24시간 후 expired.
4. 자동 저장 모드에서도 민감정보 감지 시 ask로 degrade할지 결정해야 한다.
   - 권장: 민감정보 의심 시 저장 거부 또는 ask로 degrade.

## 18. 최종 권장안

Moldy의 메모리 기능은 다음 원칙으로 구현한다.

```text
기본값은 안전하게:
  읽기 켬 + 저장 전 확인

정책은 유연하게:
  사용자 기본값 + 에이전트별 override

저장은 명시적으로:
  LLM의 raw edit_file이 아니라 memory 전용 tool

표시는 투명하게:
  proposed/saved/deleted 이벤트를 채팅 UI에 노출

저장소는 권장 구조로:
  short-term은 checkpointer
  long-term은 StoreBackend/PostgresStore
```

이 구조는 ChatGPT식 memory UX를 지원하면서도, 멀티유저/멀티에이전트 환경에서 권한과 저장 범위를 명확히 유지한다.
