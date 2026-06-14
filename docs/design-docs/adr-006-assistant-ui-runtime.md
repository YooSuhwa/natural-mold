## ADR-006: assistant-ui ExternalStoreRuntime 어댑터

### 상태: 승인됨, 2026-06-13 LangGraph v3 확장 승인

### 맥락
- 3곳(대화, 생성, AssistantPanel)의 채팅 UI를 assistant-ui 라이브러리로 통합
- 기존 백엔드 SSE API는 변경 없이 유지해야 함
- 기존 코드: Jotai atoms(streamingMessageAtom 등) + 직접 SSE 소비 패턴

### 결정
**useExternalStoreRuntime + useExternalMessageConverter** 조합 사용

1. **convert-message.ts**: `useExternalMessageConverter.Callback<Message>` 콜백
   - user → `{ role: 'user', content: string }`
   - assistant → `{ role: 'assistant', content: [text, ...tool-calls] }`
   - tool → `{ role: 'tool', toolCallId, result }` (자동 병합)

2. **use-chat-runtime.ts**: `useExternalStoreRuntime` 기반 어댑터 훅
   - TanStack Query messages + 스트리밍 중 optimistic messages 병합
   - `useExternalMessageConverter`로 ThreadMessage[] 변환
   - `onNew`: SSE AsyncGenerator 소비, 스트리밍 상태 축적
   - `onCancel`: AbortController로 스트림 취소

### 대안
- **옵션 A**: ExternalStoreAdapter.convertMessage (per-message)
  - 장점: 단순
  - 단점: tool 메시지 병합 불가 (per-message 스코프)
- **옵션 B (선택)**: useExternalMessageConverter (batch)
  - 장점: tool 메시지를 tool-call에 자동 병합, WeakMap 기반 캐싱
  - 단점: 추가 훅 호출
- **옵션 C**: 커스텀 RuntimeCore 직접 구현
  - 장점: 완전한 제어
  - 단점: 과도한 복잡도, assistant-ui 내부 API 의존

### 결과
- 기존 SSE 인프라(stream-chat.ts, stream-assistant.ts) 그대로 재사용
- Jotai atoms(streamingMessageAtom 등)은 점진적 제거 가능
- 대화/AssistantPanel 모두 동일한 useChatRuntime 훅으로 통합

### 2026-06-13 확장 결정: LangGraph v3 런타임

기존 `useExternalStoreRuntime` 결정은 legacy Moldy SSE 경로에 유지한다. 다만 DeepAgents/LangGraph v3 스트리밍을 제대로 표현하기 위해 채팅 런타임에 feature-flagged LangGraph v3 경로를 추가한다.

결정:

- `NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3`일 때 프론트는 `@langchain/react` `useStream`을 1개만 생성한다.
- 이 stream은 Moldy BFF의 conversation-scoped Agent Streaming Protocol endpoint를 사용한다.
- assistant-ui는 계속 채팅 표면을 담당하지만, 의미론적 source of truth는 LangGraph stream이다.
- `useMoldyLangGraphStream`은 root coordinator messages를 assistant-ui `useExternalStoreRuntime`으로 변환하고, raw stream은 DeepAgents state/subagent selector에 그대로 노출한다.
- HITL resume은 assistant-ui tool UI에서 decision을 모은 뒤 `stream.respond` / BFF `input.respond` / LangGraph `Command(resume=...)`로 처리한다.
- approval 이후 SDK thread lifecycle subscription이 terminal 상태에 갇히지 않도록, resume 직후 public `getThread().subscribe('lifecycle', ...)` 경로로 thread stream을 재동기화한다.
- lifecycle/input subscription은 run 단위가 아니라 thread 단위로 유지한다. BFF는 저장 이벤트 replay, 최신 live broker follow, broker rotation, idle replay throttling을 처리한다.

비결정:

- `@assistant-ui/react-langchain`의 full runtime hook을 primary로 마운트하지 않는다. Moldy가 raw `@langchain/react` stream을 직접 소유해야 subagent selector, artifacts, memory, usage, branch/replay 상태를 한 stream에서 공유할 수 있다.
- `@assistant-ui/react-langgraph`는 참고 구현/utility source로 유지하되 primary runtime으로 채택하지 않는다. 해당 adapter는 generic LangGraph assistant-ui 동작에는 적합하지만, Moldy의 root coordinator transcript와 scoped subagent transcript 분리 요구에는 직접 맞지 않는다.
- AG-UI는 외부 호환 프로토콜로 유지 가능하지만, Moldy 내부 primary runtime으로 LangGraph v3 이벤트를 AG-UI로 먼저 평탄화하지 않는다.

검증 기준:

- 단위 테스트는 `useMoldyLangGraphStream`이 한 stream만 만들고 assistant-ui로 bridge하며, `stream.respond` 후 lifecycle subscription refresh를 수행하는지 확인한다.
- 백엔드 테스트는 lifecycle/input thread stream이 broker rotation을 넘어 같은 subscription으로 다음 run 이벤트를 받는지, idle DB replay polling이 과도하지 않은지 확인한다.
- E2E는 `frontend/e2e/chat-langgraph-v3.spec.ts`에서 live state, HITL approve, subagent output, artifacts, usage tooltip, reload/replay, history, public share를 한 흐름으로 검증한다.
