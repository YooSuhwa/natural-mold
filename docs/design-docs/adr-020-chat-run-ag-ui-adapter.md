# ADR-020: Chat Run AG-UI Adapter

Status: Accepted  
Date: 2026-06-11

## Context

`docs/superpowers/plans/2026-06-10-durable-chat-run-lifecycle.md`의 P6는 향후 채팅 통신을 AG-UI로 전환할 때, 현재 구현 중인 durable run lifecycle과 충돌하지 않도록 migration seam을 미리 만들어 두는 단계다.

현재 Moldy 채팅은 다음 계약 위에서 안정화되어 있다.

- Primary POST stream: `/api/conversations/{conversation_id}/messages`
- Durable attach stream: `/api/conversations/{conversation_id}/runs/{run_id}/stream`
- 저장 이벤트: `message_events.events`
- 실행 상태: `conversation_runs`
- 프론트 소비자: `useChatRuntime`의 Moldy `SSEEvent` switch

AG-UI 공식 문서 확인 기준(2026-06-11):

- `@ag-ui/core`는 이벤트 기반 아키텍처와 core event type을 제공한다. 공식 overview는 `npm install @ag-ui/core`를 안내한다.  
  Source: <https://docs.ag-ui.com/sdk/js/core/overview>
- `@ag-ui/client`는 frontend/client 연결과 `AbstractAgent`, `HttpAgent`, middleware를 제공한다. 공식 overview는 `npm install @ag-ui/client`를 안내한다.  
  Source: <https://docs.ag-ui.com/sdk/js/client/overview>
- Core event type에는 `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR`, `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, `TOOL_CALL_RESULT`, `CUSTOM` 등이 포함된다.  
  Source: <https://docs.ag-ui.com/sdk/js/core/events>
- 공식 middleware 가이드는 기존 프로토콜을 AG-UI event로 변환하는 bridge 방식을 권장 사용 사례로 설명한다.  
  Source: <https://docs.ag-ui.com/quickstart/middleware>

## Decision

P6에서는 AG-UI SDK를 production dependency로 즉시 도입하지 않는다. 대신 backend에서 Moldy SSE 이벤트를 AG-UI core event shape로 변환하는 adapter endpoint를 추가하고, frontend는 feature flag로 그 endpoint를 소비한 뒤 기존 `SSEEvent`로 되돌린다.

이 결정의 이유:

- 현재 채팅 안정성의 핵심은 `conversation_runs`와 `message_events`에 있다. AG-UI 전환이 이 durable lifecycle을 우회하면 새로고침/세션 이동/취소 복구 기능이 다시 흔들린다.
- AG-UI 이벤트 계약은 현재 Moldy UI가 필요로 하는 usage/status/artifact/memory/interrupt 정보를 모두 1:1 표준 필드로 담지 않는다. 손실 없는 전환을 위해 `rawEvent`와 `CUSTOM.value.payload`에 Moldy payload를 보존한다.
- AG-UI SDK dependency는 전체 primary POST protocol, abort contract, message snapshot/store contract를 정리하는 다음 단계에서 넣는 편이 안전하다.

## Backend Contract

새 endpoint:

```text
GET /api/conversations/{conversation_id}/runs/{run_id}/ag-ui-stream
```

Headers:

```text
X-Run-Id: {run_id}
X-Resume-Mode: live | replay | stale
X-Stream-Protocol: ag_ui
```

Resume:

- `last_event_id` query와 `Last-Event-ID` header를 모두 지원한다.
- AG-UI 이벤트 id는 `{moldy_source_event_id}:ag:{index}` 형식이다.
- 한 Moldy 이벤트가 여러 AG-UI 이벤트로 분리되는 경우에도 DB replay에서는 AG-UI event id 단위로 정확히 이어받는다.
- Live broker attach에서는 source event가 아직 broker buffer에 있으면 AG-UI event id 단위로 이어받고, buffer 밖이면 기존 source event 기준 attach로 degrade한다.

## Event Mapping

| Moldy SSE | AG-UI |
|---|---|
| `message_start` | `RUN_STARTED` + `TEXT_MESSAGE_START` |
| `content_delta` | `TEXT_MESSAGE_CONTENT` |
| `message_end(status=completed/canceled)` | `TEXT_MESSAGE_END` + `RUN_FINISHED` |
| `message_end(status=failed)` | `TEXT_MESSAGE_END` + `RUN_ERROR` |
| `error` | `RUN_ERROR` |
| `tool_call_start` | `TOOL_CALL_START` + `TOOL_CALL_ARGS` + `TOOL_CALL_END` |
| `tool_call_result` | `TOOL_CALL_RESULT` |
| `file_event` | `CUSTOM(name="moldy.file_event")` |
| `memory_*` | `CUSTOM(name="moldy.memory_*")` |
| `interrupt` | `CUSTOM(name="moldy.interrupt")` |
| `stale` | `CUSTOM(name="moldy.stale")` |

## Frontend Contract

Feature flag:

```dotenv
NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=moldy_sse  # default
NEXT_PUBLIC_CHAT_STREAM_PROTOCOL=ag_ui
```

`streamResumeAttach`는 flag가 `ag_ui`일 때 `/ag-ui-stream`을 호출한다. 수신한 AG-UI 이벤트는 `agUiEventToMoldyEvents`로 기존 Moldy `SSEEvent`로 변환한다. 따라서 `useChatRuntime`의 렌더링, artifact, interrupt, stale, usage, cancel handling은 재작성하지 않는다.

P6의 범위는 durable attach/resume stream이다. Primary POST stream 자체의 AG-UI request/response 전환은 별도 phase에서 진행한다.

## Consequences

Positive:

- AG-UI 전환을 시작해도 durable run lifecycle, 취소, stale, replay, artifact finalization을 그대로 재사용한다.
- AG-UI event id와 Moldy source event id의 관계가 명시적이라 trace/debug가 가능하다.
- 기존 UI는 feature flag off 상태에서 완전히 동일하게 동작한다.

Tradeoffs:

- AG-UI SDK를 직접 사용하지 않으므로 runtime schema validation은 자체 테스트로 보장한다.
- Primary POST stream은 아직 Moldy SSE다. AG-UI flag는 새로고침/세션 이동/네트워크 재연결 attach 경로부터 검증한다.
- Live broker buffer 밖으로 밀린 AG-UI id는 source event 기준으로 degrade한다. DB replay에서는 AG-UI id 단위 정확도가 유지된다.
- source event마저 buffer 밖이면 `stale(reason="broker_gap")` 마커를 먼저 emit하고 buffer 잔여분 전체를 replay한다 (Moldy `/stream`과 동일한 degrade). 이때 buffer에서 `message_start`까지 evict된 긴 turn은 replay에 `TEXT_MESSAGE_START`가 없을 수 있다 — Moldy 클라이언트는 `content_delta`를 무조건 누적하므로 영향이 없지만, 표준 AG-UI 클라이언트는 START 없는 CONTENT를 버릴 수 있다. AG-UI flag를 표준 클라이언트에 노출하기 전에 gap 시 합성 `TEXT_MESSAGE_START` 주입을 검토한다.

## Verification

P6 gate:

- Backend adapter unit test: Moldy event별 AG-UI mapping과 split-event resume.
- Backend router test: live broker `/ag-ui-stream`, terminal replay `/ag-ui-stream`.
- Frontend adapter unit test: AG-UI event를 기존 Moldy `SSEEvent`로 복원.
- Frontend stream attach unit test: `NEXT_PUBLIC_CHAT_STREAM_PROTOCOL` flag delegation.
- E2E: `moldy_sse`와 `ag_ui` 두 protocol로 chat run lifecycle spec을 각각 통과.
