## ADR-006: assistant-ui ExternalStoreRuntime 어댑터

### 상태: 승인됨

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
