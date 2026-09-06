## ADR-006: assistant-ui ExternalStoreRuntime 어댑터

> **2026-09-07 현재 구현 부록.** 아래 2026-06-13 결정 기록은 당시의 맥락·대안·legacy 경로를 보존한다. 이 부록은 현재 main v3 표면의 동작만 교정하며, AG-UI 전환이나 viewer 교체를 결정하지 않는다.

## 2026-09-07 현재 구현 부록

### 런타임과 공식 UI 표면

- main `langgraph_v3`는 `useMoldyLangGraphStream`이 Moldy의 conversation-scoped LangGraph stream과 raw state/activity를 소유하고, 결과를 assistant-ui `useExternalStoreRuntime`으로 bridge한다. primary transport는 이 custom LangGraph 경로이며, `@assistant-ui/react-langchain` full runtime hook이나 AG-UI를 primary로 마운트하지 않는다.
- `AssistantPanel`과 conversational Builder는 legacy `useChatRuntime`을 유지한다. 모든 채팅 표면이 한 hook을 공유한다고 가정하지 않는다.
- 현재 공개 assistant-ui API는 `AuiConfig`, `Tools`, `defineToolkit`, `useAui`, `useAuiState`를 사용한다. main chat/AssistantPanel은 HITL을 포함한 `ALL_TOOLKIT`, settings TestChatPanel은 `SETTINGS_TEST_TOOLKIT`, Builder는 `BUILDER_TOOLKIT`을 사용한다.
- 해석된 주요 버전은 `@assistant-ui/react` 0.15.18, assistant-ui core 0.3.17, `@langchain/react` 1.0.35이다. lockfile에서 직접 `@langchain/langgraph-sdk`는 1.9.22이고 React 의존성 아래 중첩 SDK는 1.10.2이므로 둘을 하나의 버전으로 평탄화하지 않는다.

### 현재 대화 계약과 한계

- 일반 입력은 durable queue input으로 접수되고, 서버 claim 뒤에만 active run이 생긴다. `run_id` 없는 accepted pending input은 실패가 아니다.
- Steer는 우선 정정 입력을 durable하게 접수하고 predecessor 취소를 요청·확인한 뒤 committed state에서 **새 run**을 시작한다. same-run Steer는 현재 run을 보존한 채 다음 agent step에서 새 지시를 소비하는 별도 계약이며 구현하지 않았다. 이는 실행 중인 provider request의 token을 수정·주입하는 것과도 다르다. checkpoint에 아직 반영되지 않은 외부 tool effect에는 범용 exactly-once 보장이 없다.
- 실패 retry는 정확한 failed durable input을 새 client request ID로 재접수하고, 불확실한 응답은 같은 request ID로 한 번 reconcile한다. 성공 turn regenerate나 checkpoint fork가 아니다.
- slash command는 실제 capability만 연다. `/search`는 rendered transcript 검색, `/export`는 로드된 envelope의 Markdown/JSON export이며 backend full-history/PDF export가 아니다. `/compact`는 인증된 수동 action이 없어 disabled다. 알 수 없거나 불가한 command를 모델에 조용히 보내지 않는다.
- `@file`, `@artifact`, `@skill`, `@conversation`은 multimodal model input이 아니라 authorize 후 고정한 text snapshot이다. 최대 8개, 각 UTF-8 32 KiB, 합계 128 KiB이며, dispatch 때 권한을 다시 확인하되 새 콘텐츠로 snapshot을 교체하지 않는다. label은 표시용이다.
- terminal metrics는 nullable replay-safe snapshot이다. 누락된 capture는 0이 아니라 `unknown`/`null`이며 root/descendant tool·subagent count와 inclusive total을 구분한다. activity가 잘리면 `activity_truncated`으로 알리고, usage 없는 text-only response는 elapsed time만 있고 activity/token이 비어 있을 수 있다.
- MCP App은 conversation/run/tool/server provenance로 범위가 정해진 backend proxy와 sandbox renderer를 사용한다. metadata는 권한을 부여하지 않고 browser credential은 노출하지 않는다. 공식 renderer가 capability를 표시할 수 있어도 Moldy는 `openLink`와 `sendMessage`를 명시적으로 deny하며, widget은 보이는 action에 policy error를 받을 수 있다.
- side chat은 같은 user/agent의 app-shell에서 close/reopen과 same-tab navigation 동안만 유지되고 user 전환 시 초기화된다. reload/cross-device persistence나 main conversation을 side transcript 저장소로 쓰는 동작은 제공하지 않는다. pinned summary는 user-selected display snapshot일 뿐 memory/prompt injection/automatic summary가 아니다. dictation은 editable composer에서 browser `SpeechRecognition` adapter를 사용하며, 별도 transcription backend나 credential flow는 없다. QA는 speech double을 사용하고 실제 microphone end-to-end는 검증하지 않았다.

### 구현 및 검증 경로

주요 구현 앵커는 `frontend/src/lib/chat/langgraph-runtime/use-moldy-langgraph-stream.ts`, `frontend/src/components/chat/chat-runtime-section.tsx`, `frontend/src/lib/chat/tool-ui-registry.ts`, `backend/app/services/conversation_run_worker.py`, `backend/app/services/chat_resource_context.py`, `frontend/src/lib/chat/mcp-apps/renderer.tsx`, `frontend/src/components/agent/assistant-side-chat-provider.tsx`이다.

기능별 scripted-capture catalog는 `frontend/e2e/chat-message-queue.spec.ts`, `chat-commands-context.spec.ts`, `chat-run-summary.spec.ts`, `chat-mcp-apps.spec.ts`, `chat-recovery-discovery.spec.ts`, `chat-dictation.spec.ts`이다. 전체 suite 대신 다음처럼 하나의 catalog spec만 실행한다.

```sh
# Select Node 22 with the local toolchain manager first; node --version must print v22.x.
node --version
cd "$(git rev-parse --show-toplevel)"
manifest=".omo/evidence/project-restart-consolidated-roadmap/chat-recovery-discovery-$(date -u +%Y%m%dT%H%M%SZ).json"
NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3 E2E_TEST_HELPERS_ENABLED=true RATE_LIMIT_ENABLED=false E2E_CAPTURE_TOUR=1 \
  bash scripts/run-isolated-e2e-tests.sh scripted --project scripted-capture \
  --manifest "$manifest" \
  -- e2e/chat-recovery-discovery.spec.ts --workers=1 --retries=0
```

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
