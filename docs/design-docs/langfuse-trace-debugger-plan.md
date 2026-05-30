# Moldy Langfuse Trace Debugger 개발 기획서

작성일: 2026-05-30

## 1. 배경

Moldy는 사용자별 Agent를 만들고, 각 Agent와 대화 세션을 생성해 실행하는
구조를 가진다. 현재 세션 URL은 다음 형태다.

```text
/agents/{agent_id}/conversations/{conversation_id}
```

예시:

```text
http://127.0.0.1:3000/agents/25f1cb9f-ab05-4146-b890-a22452e3c942/conversations/42c6d343-3a3f-4119-8d0a-1459a3967776
```

Moldy 내부에는 이미 `message_events`가 있어 assistant turn 단위 SSE 이벤트를
저장한다. 이 데이터는 SSE resume, 공유 페이지의 tool/skill chip 렌더링,
stream 복구에 적합하다. 하지만 LangChain/LangGraph/Deep Agents의 내부 실행
흐름, LLM 호출, tool span, middleware, retry, latency를 waterfall 형태로
디버깅하기에는 trace backend로서의 기능이 부족하다.

따라서 Langfuse를 trace 수집/저장 backend로 사용하고, Moldy UI에서는
Agent Prism 기반의 trace viewer를 제공한다.

## 2. 현재 Langfuse 환경

- Langfuse URL: `https://langfuse-dev.apps.orca.cloud.hancom.com/`
- Langfuse version: `v3.150`
- Organization: `Protech`
- Project: `moldy`
- API key: backend env 또는 운영 secret으로 주입

Langfuse Python SDK v3는 self-hosted Langfuse platform `>=3.125.0`을 요구한다.
현재 설치된 `v3.150`은 SDK v3 기반 연동에 적합하다.

## 3. 목표

1. Moldy의 user - agent - conversation 구조를 Langfuse trace에 명확히 반영한다.
2. 기존 conversation URL 안에서 trace debug 화면으로 진입할 수 있게 한다.
3. Langfuse에 저장된 trace를 Moldy backend가 proxy로 조회한다.
4. frontend는 Agent Prism을 사용해 span tree, waterfall, detail panel을 렌더링한다.
5. `message_events`는 기존 기능을 유지하고, Langfuse trace와 correlation만 추가한다.

## 4. 비목표

- Langfuse를 Moldy의 대화 원본 저장소로 사용하지 않는다.
- Langfuse secret key를 browser에 노출하지 않는다.
- public share page에 trace debug 정보를 노출하지 않는다.
- 초기 버전에서 Langfuse 자체 UI의 모든 기능을 재구현하지 않는다.
- Agent Prism alpha API에 Moldy core runtime을 강결합하지 않는다.

## 5. 핵심 ID 매핑

| Moldy | Langfuse | 설명 |
| --- | --- | --- |
| `users.id` | `user_id` | 실행 주체. 개인정보 최소화를 위해 UUID 우선 사용 |
| `agents.id` | metadata `moldy_agent_id` | Agent 단위 필터링 |
| `conversations.id` | `session_id` | 대화 세션. Moldy URL의 conversation id와 동일 |
| `message_events.assistant_msg_id` 또는 stream `run_id` | trace id seed 또는 metadata `moldy_run_id` | assistant turn 단위 실행 id |
| `Conversation.active_branch_checkpoint_id` | metadata `moldy_checkpoint_id` | branch/debug 추적 |
| frontend route | metadata `moldy_route` | Moldy 화면으로 돌아가는 링크 |

권장 trace 단위:

```text
Langfuse trace = Moldy assistant turn 1회
Langfuse session = Moldy conversation 1개
Langfuse user = Moldy user 1명
```

## 6. Metadata 계약

Langfuse callback에 다음 metadata를 주입한다.

```python
metadata = {
    "langfuse_user_id": str(user_id),
    "langfuse_session_id": str(conversation_id),
    "langfuse_tags": ["moldy", "agent-chat"],
    "moldy_user_id": str(user_id),
    "moldy_agent_id": str(agent_id),
    "moldy_conversation_id": str(conversation_id),
    "moldy_run_id": run_id,
    "moldy_model_id": str(model_id) if model_id else None,
    "moldy_checkpoint_id": checkpoint_id,
    "moldy_route": f"/agents/{agent_id}/conversations/{conversation_id}",
    "moldy_source": "chat",
}
```

`moldy_source` 값:

- `chat`
- `resume`
- `edit`
- `regenerate`
- `trigger`
- `builder`

## 7. Backend 설계

### 7.1 환경 변수

```env
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://langfuse-dev.apps.orca.cloud.hancom.com/
LANGFUSE_PROJECT=moldy
```

`LANGFUSE_PROJECT`는 사람이 읽는 운영 문서/로그용이다. 실제 project binding은
Langfuse API key가 속한 project로 결정된다.

### 7.2 의존성

Moldy backend는 Python SDK v3 계열을 사용한다.

```toml
langfuse>=3.8,<4.0
```

LangChain 1.x 지원을 위해 최소 `3.8` 이상을 사용한다. Langfuse platform이
v3.150이므로 초기 도입에서는 SDK v4 대신 SDK v3에 맞춘다.

### 7.3 Runtime hook

`executor.py`에서 LangGraph config를 생성하는 지점에 Langfuse callback을
선택적으로 주입한다.

```python
from langfuse.langchain import CallbackHandler

handler = CallbackHandler()

config["callbacks"] = [handler]
config["metadata"] = metadata
config["tags"] = ["moldy", f"source:{source}"]
```

기존 config의 `configurable.thread_id`는 유지한다.

```python
config = {
    "configurable": {"thread_id": cfg.thread_id},
    "callbacks": [handler],
    "metadata": metadata,
    "tags": ["moldy", "agent-chat"],
}
```

### 7.4 Trace correlation 저장

`message_events`는 기존 SSE 이벤트 저장 책임을 유지한다. Langfuse correlation을
위해 다음 컬럼을 추가하는 방안을 검토한다.

```text
external_trace_provider: "langfuse" | null
external_trace_id: string | null
external_trace_url: string | null
```

초기 구현에서는 `external_trace_id = run_id` 또는 deterministic trace id로
맞춘다. Langfuse trace id를 SDK가 생성하는 경우에는 finalize 시점에 trace id를
알 수 있는지 검증하고 저장한다.

### 7.5 Backend proxy API

Langfuse secret key는 backend에만 존재해야 한다. frontend는 Moldy API만 호출한다.

```text
GET /api/conversations/{conversation_id}/debug/traces
```

동작:

1. 현재 사용자 인증
2. `conversation_id` ownership 검증
3. Langfuse에서 `session_id = conversation_id` 기준 trace 목록 조회
4. Moldy용 summary shape로 반환

응답 예시:

```json
{
  "conversation_id": "42c6d343-3a3f-4119-8d0a-1459a3967776",
  "traces": [
    {
      "trace_id": "457165b4f06a19b5ba8a0830bd49e8d",
      "name": "agent.chat",
      "status": "success",
      "started_at": "2026-04-13T13:41:17Z",
      "duration_ms": 798380,
      "total_tokens": 258903,
      "moldy_run_id": "457165b4f06a19b5ba8a0830bd49e8d",
      "langfuse_url": "https://langfuse-dev.apps.orca.cloud.hancom.com/..."
    }
  ]
}
```

```text
GET /api/conversations/{conversation_id}/debug/traces/{trace_id}
```

동작:

1. 현재 사용자 인증
2. `conversation_id` ownership 검증
3. `trace_id`가 해당 conversation/session에 속하는지 검증
4. Langfuse trace observations/spans 조회
5. Agent Prism 또는 Moldy Trace UI가 소비 가능한 shape로 반환

## 8. Frontend 설계

### 8.1 Route

기존 conversation URL을 유지한다.

```text
/agents/{agent_id}/conversations/{conversation_id}
```

디버그 화면은 같은 세션 context 안에서 진입한다.

권장 deep link:

```text
/agents/{agent_id}/conversations/{conversation_id}/debug
/agents/{agent_id}/conversations/{conversation_id}/debug?traceId={trace_id}
```

초기 버전에서는 기존 conversation page에 side panel 또는 drawer로 추가하고,
후속으로 child route를 도입할 수 있다.

### 8.2 화면 구성

첨부 이미지와 유사한 3-pane 구조를 목표로 한다.

```text
왼쪽: Run 정보 및 필터
가운데: Span tree / waterfall
오른쪽: Span 상세
```

왼쪽 패널:

- 실행 상태
- 시작/종료 시각
- 수행 시간
- token/cost
- source 필터: chat/resume/edit/regenerate/trigger
- 개인정보/기밀정보/유해정보 필터 placeholder
- Langfuse 원본 열기

가운데 패널:

- span 검색
- span tree
- waterfall toggle
- workflow / LLM / tool / HTTP / MCP / skill badge
- duration 표시
- success/error/interrupted 상태 표시

오른쪽 패널:

- `Run`
- `Metadata`
- `Filtering`
- input/output tab
- model/provider 정보
- prompt/tool args/tool result
- raw JSON 보기

### 8.3 Agent Prism 사용 전략

Agent Prism은 Langfuse adapter와 React 기반 trace viewer 컴포넌트를 제공한다.
초기 POC에서는 Agent Prism의 viewer 컴포넌트를 최대한 그대로 사용한다.

검증 항목:

- React 19 호환성
- Moldy Tailwind v4와 Agent Prism Tailwind v3 스타일 충돌 여부
- shadcn/ui token과의 시각적 일관성
- Agent Prism alpha API 변경 가능성

리스크 완화:

1. Agent Prism을 Moldy core component와 분리된 debug module로 둔다.
2. adapter layer를 Moldy 내부에 둬서 Agent Prism API 변경 영향을 줄인다.
3. 스타일 충돌이 크면 data adapter만 사용하고 UI는 Moldy 컴포넌트로 재구현한다.

## 9. 권한 및 보안

1. 일반 사용자는 본인 conversation trace만 조회할 수 있다.
2. super_user는 운영자 debug route에서 전체 trace 접근을 허용할 수 있다.
3. public share page에는 debug trace를 노출하지 않는다.
4. Langfuse API secret은 backend env/secret에만 둔다.
5. frontend에는 Langfuse public/secret key를 전달하지 않는다.
6. credential-like key는 전송 전 redaction한다.
7. system prompt, user input, tool result 저장 여부는 운영 설정으로 제어한다.

추가 설정 후보:

```env
LANGFUSE_CAPTURE_INPUT_OUTPUT=true
LANGFUSE_REDACTION_ENABLED=true
LANGFUSE_SAMPLE_RATE=1.0
```

## 10. 장애 및 fallback

Langfuse 장애는 Moldy 채팅 실행을 막지 않아야 한다.

정책:

- Langfuse callback 초기화 실패 시 warning log 후 tracing 비활성화
- Langfuse 전송 실패 시 agent execution은 계속 진행
- Debug UI에서 Langfuse 조회 실패 시 Moldy `message_events` 기반 최소 trace를 표시
- `external_trace_id`가 없는 과거 대화는 debug unavailable 상태로 표시

## 11. 개발 단계

### Phase 1. Langfuse 수집 POC

- backend dependency 추가
- env config 추가
- `executor.py`에 Langfuse CallbackHandler 주입
- Langfuse UI에서 user/session/metadata가 의도대로 보이는지 확인
- chat/resume/edit/regenerate 각각 trace 생성 확인

완료 기준:

- Langfuse `moldy` project에 trace가 생성된다.
- `conversation_id`로 session grouping이 된다.
- `user_id`, `agent_id`, `run_id`가 metadata에 들어간다.

### Phase 2. Correlation 저장

- `message_events`에 external trace 컬럼 추가
- stream `run_id`와 Langfuse trace id 매핑
- trace URL 생성 helper 추가

완료 기준:

- Moldy 대화 turn에서 Langfuse 원본 trace로 이동할 수 있다.
- `message_events`와 Langfuse trace가 1:1로 연결된다.

### Phase 3. Backend Debug API

- trace list endpoint 추가
- trace detail endpoint 추가
- ownership 검증 추가
- Langfuse API client wrapper 추가

완료 기준:

- conversation page에서 해당 conversation의 trace 목록을 조회할 수 있다.
- 다른 사용자의 conversation trace는 조회할 수 없다.

### Phase 4. Frontend Debug UI

- conversation page에 Debug 진입점 추가
- trace list panel 구현
- Agent Prism viewer POC 적용
- span detail panel 연결

완료 기준:

- 첨부 이미지와 유사한 3-pane trace debug 화면이 동작한다.
- span 선택 시 input/output/metadata를 볼 수 있다.
- waterfall toggle이 동작한다.

### Phase 5. 품질 보강

- redaction 테스트
- Langfuse 장애 fallback 테스트
- token/cost/duration 표시 보강
- trigger run trace 표시
- raw JSON download 또는 copy 기능

## 12. 테스트 계획

Backend:

- Langfuse disabled 상태에서 기존 chat 정상 동작
- Langfuse enabled 상태에서 callback 주입
- ownership 검증
- trace list/detail API 권한 테스트
- Langfuse API failure fallback
- metadata shape regression

Frontend:

- conversation route에서 Debug panel open/close
- trace list loading/error/empty
- span tree rendering
- span selection detail rendering
- mobile/desktop layout
- long prompt/tool result overflow 처리

수동 검증:

- Langfuse UI에서 session grouping 확인
- Moldy Debug UI와 Langfuse 원본 trace의 duration/token 비교
- chat/resume/edit/regenerate/trigger source 구분 확인

## 13. 주요 리스크

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| Agent Prism alpha API 변경 | UI 유지보수 비용 증가 | adapter layer 분리 |
| Tailwind v3/v4 충돌 | 스타일 깨짐 | debug module CSS 격리 |
| Langfuse API shape 변경 | backend proxy 깨짐 | Langfuse client wrapper로 격리 |
| 과도한 prompt/input 저장 | 개인정보/보안 리스크 | capture 설정, redaction |
| Langfuse 장애 | Debug UI 조회 실패 | message_events fallback |
| trace id 불일치 | Moldy turn과 trace 연결 실패 | deterministic run_id 기반 mapping |

## 14. 결정 사항

1. `conversation_id`를 Langfuse `session_id`로 사용한다.
2. `user.id`를 Langfuse `user_id`로 사용한다.
3. Langfuse trace는 assistant turn 단위로 생성한다.
4. Moldy `message_events`는 유지하고 Langfuse trace correlation만 추가한다.
5. frontend는 Langfuse에 직접 접근하지 않고 backend proxy를 사용한다.
6. Agent Prism은 초기 POC에 사용하되 Moldy core와 분리한다.

## 15. 참고 자료

- Langfuse Sessions: https://langfuse.com/docs/observability/features/sessions
- Langfuse LangChain tracing: https://langfuse.com/integrations/frameworks/langchain
- Langfuse Python SDK v3: https://langfuse.com/docs/sdk/python/sdk-v3
- Agent Prism: https://github.com/evilmartians/agent-prism
