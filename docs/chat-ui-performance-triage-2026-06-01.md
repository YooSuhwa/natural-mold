# Chat UI Performance Triage

작성일: 2026-06-01
현재 HEAD: `7ac1448`
입력 문서:

- `/Users/chester/Downloads/performance-audit.md`
- `/Users/chester/Downloads/chat-ui-performance-audit.md`

## 목적

두 감사 문서의 성능 개선 후보를 현재 소스코드와 직접 대조해, 지금 채팅 UI 리팩토링에서 진행할 항목과 별도 트랙으로 분리할 항목을 구분한다.

처음 작성 시점에는 의사결정 기록으로 시작했고, 이후 같은 문서에 실제 구현 완료 현황과 검증 결과를 추가했다.

## 확인 방법

1. 두 감사 문서의 우선순위 항목과 상세 분석을 읽었다.
2. 현재 HEAD 기준으로 실제 파일과 라인 위치를 다시 확인했다.
3. 채팅 UI 관련 항목은 React/Next 성능 관점에서 분류했다.
4. LangGraph/Deep Agents 관련 항목은 LangChain 계층 선택, persistence, Deep Agents backend/memory 지침과 대조했다.
5. 실제 프로파일링은 아직 수행하지 않았다. 따라서 일부 항목은 "진행"이 아니라 "측정 후 진행"으로 분리했다.

## 요약 결론

채팅 UI 리팩토링의 1차 범위는 다음 다섯 가지로 잡는 것이 좋다.

1. 이전 stream cleanup race 방지
2. render phase state update 제거
3. streaming 중 code block syntax highlighting 비활성화
4. streaming flush마다 발생하는 전체 메시지 재계산 축소
5. 큰 tool result 렌더 비용을 lazy/memo 처리

전역 성능 문서의 DB index, `/api/agents` 경량화, checkpoint 조회 최적화, trace 저장 구조, DeepAgent runtime cache는 모두 유효한 지적이지만 채팅 UI 깜박임 리팩토링과 같은 PR에 섞지 않는 것이 좋다. 이들은 백엔드/런타임 성능 트랙으로 별도 설계가 필요하다.

## 구현 완료 현황

완료일: 2026-06-01

사용자가 "별도 진행은 여기서 진행 안할꺼야"라고 범위를 좁혔기 때문에, 아래 완료 현황은 Chat UI 1차/진행 가능 항목만 포함한다. Backend/API/DB/Runtime/Deep Agents 트랙의 "별도 진행" 항목은 이번 작업에서 구현하지 않았다.

| 항목 | 상태 | 반영 내용 | 대표 검증 |
| --- | --- | --- | --- |
| Stream stale guard 보강 | 완료 | rAF callback, stream `finally`, `onStreamEnd`, final commit 경로에 stale token guard를 추가하고 pending rAF를 취소한다. | `use-chat-runtime-commit.test.tsx` stale stream cleanup 회귀 테스트 |
| Render phase state update 제거 | 완료 | render 중 `setPrevMessages`/`setStreamingMessages` 호출을 effect로 이동하고 cheap message key를 먼저 비교한다. | `use-chat-runtime-commit.test.tsx` commit/refetch 회귀 테스트 |
| Streaming code block plain render | 완료 | streaming 중 fenced code block은 `SyntaxHighlighter` 없이 plain `<pre><code>`로 렌더한다. 완료 후에는 기존 highlighter 경로를 유지한다. | `markdown-content.test.tsx` streaming code block 테스트 |
| Streaming projection/usage 비용 축소 | 완료 | token usage 계산을 persisted/streaming 합산으로 분리하고 동일 값 atom update를 막는다. `allMessages` merge도 불필요한 배열 생성을 줄였다. | token usage no-repeat update 테스트, 전체 Vitest |
| Tool UI JSON lazy/memo | 완료 | tool args/result stringify, image URL extraction, right rail JSON parse/pretty stringify를 memoize하고 열린 패널 기준으로 계산한다. | `collapsible-pill.test.tsx`, `tool-result-panel-content.test.tsx` |
| Right rail conversation reset | 완료 | right rail payload에 `conversationId`를 연결하고 현재 대화와 다르면 stale panel을 숨기거나 reset한다. | `chat-right-rail.test.tsx` |
| SSE queue `shift()` 제거 | 완료 | POST SSE bridge queue를 head-index queue로 바꿔 slow consumer 상황의 O(n) `shift()` 비용을 제거했다. | `parse-sse.test.ts` head-index queue 테스트 |

### 구현 중 추가로 정리한 E2E 안정화

전체 E2E 완료를 막던 테스트 fixture 차이도 함께 정리했다.

- `/api/models?include_hidden=true` 요청을 `**/api/models` glob mock이 잡지 못하던 문제를 query string 포함 regex로 수정했다.
- 실제 도구 타입 seed의 한국어 표시명 `HTTP 요청`과 기존 테스트의 `HTTP Request` 기대값이 어긋나던 부분을 양쪽 표현을 모두 허용하도록 수정했다.
- Next dev cold compile에서 첫 E2E가 30초 timeout을 넘는 문제가 있어 Playwright 기본 timeout을 60초로 올렸다.
- Playwright webServer의 frontend command를 `pnpm exec next dev --port 3000`으로 고정해 임의 포트 선택을 방지했다.

### 최종 검증 결과

아래 검증은 2026-06-01에 현재 worktree에서 실행했다.

| 명령 | 결과 |
| --- | --- |
| `pnpm vitest run` | 통과. 75 files, 381 tests |
| `pnpm build` | 통과 |
| `pnpm lint` | 통과. 기존 warning 3개만 남음, error 0개 |
| `git diff --check` | 통과 |
| `E2E_BASE_URL=http://127.0.0.1:3000 E2E_API_BASE_URL=http://127.0.0.1:8001 NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001 pnpm exec playwright test --workers=1` | 통과. 33 passed, 2 skipped |

남아 있는 lint warning은 이번 작업과 무관한 기존 테스트 파일의 unused symbol이다.

- `frontend/tests/components/chat/assistant-thread-actions.test.tsx`
- `frontend/tests/components/marketplace/marketplace-copy.test.tsx`

## 판정 표

| 구분 | 항목 | 판정 | 이유 |
| --- | --- | --- | --- |
| Chat UI 1차 | Stream stale guard 보강 | 진행 | 이전 stream의 rAF/finally cleanup이 새 stream 상태를 덮을 수 있다. |
| Chat UI 1차 | render phase state update 제거 | 진행 | refetch 전환 중 추가 render와 깜박임을 만들 수 있다. |
| Chat UI 1차 | streaming code block plain render | 진행 | 긴 코드 응답에서 syntax highlighter가 가장 직접적인 main-thread 비용 후보다. |
| Chat UI 1차 | streaming 전체 메시지 재계산 축소 | 진행, 단계적 | 구조 개선 효과가 크지만 assistant-ui 연동 경계라 테스트가 필요하다. |
| Chat UI 1차 | Tool UI JSON 계산 lazy/memo | 진행 | 큰 tool result에서 렌더 경로를 막을 수 있다. |
| Chat UI 1차/후속 | Right rail conversation reset | 진행 가능 | 성능보다 잘못된 상태 표시 방지에 가깝지만 작고 안전하다. |
| Chat UI 후속 | SSE queue `shift()` 제거 | 진행 가능 | 작은 개선. 실제 체감은 slow consumer 상황에서만 드러난다. |
| 측정 우선 | `content-visibility:auto` 조정 | 측정 후 | 긴 리스트 최적화로 유효하다. 스크롤 점프 재현 전 제거는 이르다. |
| 후순위 | ChatPage title/read 구독 정리 | 후순위 | stale correctness 문제이며 streaming 깜박임의 핵심 원인은 아니다. |
| 후순위 | conversation list virtualization | 후순위 | 대화 수가 많을 때 필요하지만 현재 stream 깜박임과 직접 관련은 낮다. |
| 후순위 | `CollapsiblePill` auto expand 정책 | 후순위 | 성능보다 UX 일관성 문제다. |
| 이미 개선 | `content_delta`별 즉시 state update | 모니터링 | rAF batching이 이미 들어가 있다. |
| Backend 트랙 | `/api/agents` 집계/인덱스 | 별도 진행 | 유효하지만 DB/API 성능 작업이다. |
| Backend 트랙 | message read path write 제거 | 별도 진행 | 유효하지만 persistence/projection 설계가 필요하다. |
| Backend 트랙 | checkpoint 중복 순회 제거 | 별도 진행 | LangGraph persistence 경로라 별도 테스트가 필요하다. |
| Backend 트랙 | trace JSON row rewrite 개선 | 별도 진행 | schema 변경 가능성이 높다. |
| Runtime 트랙 | DeepAgent/runtime cache | 별도 설계 | cache key, invalidation, tenant/credential isolation이 필요하다. |
| 비권장 | checkpointer 제거, `thread_id` 생략 | 하지 않음 | HITL, branch, time travel, resume을 깨뜨린다. |
| 비권장 | Deep Agents를 단순 LangChain agent로 하향 | 하지 않음 | 제품 요구가 Deep Agents/LangGraph 계층과 맞는다. |

## Chat UI 1차 진행 항목

### 1. Stream stale guard 보강

#### 감사 문서 주장

이전 stream의 `finally` 또는 예약된 rAF callback이 새 stream 상태를 덮을 수 있다.

#### 현재 소스 확인

`useChatRuntime`은 새 stream을 시작할 때 이전 `AbortController`를 abort하고 stream guard token을 갱신한다.

- `frontend/src/lib/chat/use-chat-runtime.ts:287`
- `frontend/src/lib/chat/use-chat-runtime.ts:297`

SSE 이벤트 처리 루프 안에서는 stale token을 확인한다.

- `frontend/src/lib/chat/use-chat-runtime.ts:414`
- `frontend/src/lib/chat/use-chat-runtime.ts:417`

하지만 다음 위치는 token guard가 없다.

- rAF callback: `frontend/src/lib/chat/use-chat-runtime.ts:402`
- rAF flush state update: `frontend/src/lib/chat/use-chat-runtime.ts:405`
- `finally` cleanup: `frontend/src/lib/chat/use-chat-runtime.ts:578`
- `setIsRunning(false)`: `frontend/src/lib/chat/use-chat-runtime.ts:579`
- final streaming state 반영: `frontend/src/lib/chat/use-chat-runtime.ts:596`
- `onStreamEnd`: `frontend/src/lib/chat/use-chat-runtime.ts:603`

#### 판단

진행해야 한다. 사용자가 응답 중 stop, 새 메시지, edit, regenerate를 빠르게 실행하면 이전 stream cleanup이 새 stream의 `isRunning`, `streamingMessages`, query refetch 타이밍을 흔들 수 있다.

#### 구현 방향

- rAF id를 저장하고 stream 종료/abort/stale 시 취소한다.
- rAF callback 안에서 `streamGuardRef.current.isStale(token)`을 다시 확인한다.
- `finally` 초입에서 stale이면 UI state update와 `onStreamEnd`를 건너뛴다.
- `onFailed`처럼 `onStreamEnd`도 stale stream에서는 refetch를 일으키지 않게 한다.
- interrupt로 정상 pause된 stream은 refetch가 필요하므로 stale guard와 interrupt 종료를 구분한다.

#### 검증

- 응답 중 Stop 후 즉시 새 메시지 전송
- 응답 중 Regenerate 후 즉시 Stop
- Edit 직후 Regenerate
- 네트워크 resume 중 새 메시지 전송
- stale stream에서 `onStreamEnd`가 호출되지 않는 단위 테스트

### 2. Render phase state update 제거

#### 감사 문서 주장

`useChatRuntime` 하단에서 render 중 `setPrevMessages`, `setStreamingMessages`가 호출될 수 있다.

#### 현재 소스 확인

현재 코드는 render phase에서 이전 messages snapshot을 비교한다.

- `frontend/src/lib/chat/use-chat-runtime.ts:620`
- `frontend/src/lib/chat/use-chat-runtime.ts:621`

조건이 맞으면 render 중 state update가 발생한다.

- `setPrevMessages(messages)`: `frontend/src/lib/chat/use-chat-runtime.ts:622`
- `setStreamingMessages([])`: `frontend/src/lib/chat/use-chat-runtime.ts:625`
- `setStreamingMessages((sm) => ...)`: `frontend/src/lib/chat/use-chat-runtime.ts:631`

#### 판단

진행해야 한다. React에서 이전 render 정보를 저장하는 패턴 자체는 가능하지만, 이 코드에서는 streaming cleanup까지 같이 수행한다. refetch 직후 화면 전환 구간에서 추가 render, optimistic user 제거 타이밍 문제, 깜박임을 만들 수 있다.

#### 구현 방향

- `prevMessages` 비교와 streaming cleanup을 `useEffect`로 이동한다.
- `sameMessageSnapshot` 전체 비교 전에 cheap key를 먼저 둔다.
  - length
  - first/last id
  - last assistant id
  - active checkpoint id가 있다면 envelope 쪽 key 활용
- effect 내부에서 stale/running 상태를 명확히 확인한다.
- interrupted/partial stream 보존 규칙은 유지한다.

#### 검증

- 기존 `use-chat-runtime-commit.test.tsx` 회귀 테스트 유지
- refetch 후 streaming assistant가 깜박이지 않는 테스트 추가
- mid-stream 끊김에서 partial assistant가 보존되는 테스트 유지
- optimistic user만 중복 제거되는 테스트 추가

### 3. Streaming code block plain render

#### 감사 문서 주장

streaming 중 일반 fenced code block이 syntax highlighter를 타면 긴 코드 응답에서 main thread block이 발생할 수 있다.

#### 현재 소스 확인

`AssistantThread`는 message status가 running이면 streaming용 markdown components를 사용한다.

- `frontend/src/components/chat/assistant-thread.tsx:96`
- `frontend/src/components/chat/assistant-thread.tsx:100`

`buildMarkdownComponents({ isStreaming })`는 mermaid만 streaming 중 raw code로 둔다.

- `frontend/src/components/chat/markdown-content.tsx:186`
- `frontend/src/components/chat/markdown-content.tsx:187`

일반 fenced code block은 streaming 여부와 관계없이 `CodeBlock`을 렌더한다.

- `frontend/src/components/chat/markdown-content.tsx:181`
- `frontend/src/components/chat/markdown-content.tsx:194`

`CodeBlock`은 `SyntaxHighlighter`를 사용한다.

- `frontend/src/components/chat/markdown-content.tsx:43`
- `frontend/src/components/chat/markdown-content.tsx:80`

#### 판단

진행해야 한다. 구현 난도가 낮고 효과가 직접적이다. LLM이 300줄 이상 코드 파일을 생성하는 경우 rAF batching만으로는 부족할 수 있다.

#### 구현 방향

- `isStreaming === true`이면 모든 fenced code block을 plain `<pre><code>`로 렌더한다.
- copy button은 streaming 중에도 유지할지 결정한다.
  - 최소 변경: plain block에도 header/copy UI 유지
  - 더 가벼운 변경: streaming 중 header/copy 없이 plain block
- message complete 후 기존 `SyntaxHighlighter` 경로로 전환한다.
- line count가 큰 final code block에도 highlighter를 끄는 threshold를 추가할지 후속으로 검토한다.

#### 검증

- 300줄 code block streaming 시 long task 감소 확인
- streaming 중 code block이 깨지지 않고 plain text로 표시되는지 확인
- message complete 후 syntax highlight가 적용되는지 확인
- copy button 동작 유지 여부 확인

### 4. Streaming 중 전체 메시지 재계산 축소

#### 감사 문서 주장

content flush마다 전체 메시지 merge, token usage 합산, assistant-ui 변환이 반복된다.

#### 현재 소스 확인

streaming flush가 일어나면 `streamingMessages`가 새 배열로 갱신된다.

- `frontend/src/lib/chat/use-chat-runtime.ts:395`
- `frontend/src/lib/chat/use-chat-runtime.ts:405`
- `frontend/src/lib/chat/use-chat-runtime.ts:447`
- `frontend/src/lib/chat/use-chat-runtime.ts:494`

그 결과 `allMessages`가 전체 messages와 streamingMessages를 다시 병합한다.

- `frontend/src/lib/chat/use-chat-runtime.ts:308`
- `frontend/src/lib/chat/use-chat-runtime.ts:311`

이후 token usage가 전체 `allMessages`를 순회해 합산된다.

- `frontend/src/lib/chat/use-chat-runtime.ts:325`
- `frontend/src/lib/chat/use-chat-runtime.ts:329`

assistant-ui 변환도 `allMessages` 전체를 입력으로 받는다.

- `frontend/src/lib/chat/use-chat-runtime.ts:343`
- `frontend/src/lib/chat/use-chat-runtime.ts:346`

#### 판단

진행하되 단계적으로 해야 한다. 이 항목은 효과가 크지만 assistant-ui ExternalStoreRuntime과 연결된 핵심 경로라 작은 변경부터 적용해야 한다.

#### 1단계 구현 방향

- token usage 합산을 매 content flush가 아니라 다음 시점에 제한한다.
  - fetched messages 변경
  - `message_end`
  - 일정 interval
- `setTokenUsage`에 동일 값이면 update하지 않는 guard를 둔다.
- `messages`와 `streamingMessages` 병합에서 `[...messages, ...streamingMessages]` 임시 배열 생성을 피한다.

#### 2단계 구현 방향

- persisted conversation runtime과 local ephemeral runtime의 commit 전략을 분리한다.
- 공통 stream consumer는 유지하되, refetch-driven path와 local commit path를 adapter로 분리한다.
- streaming assistant message projection만 별도로 변환할 수 있는지 assistant-ui API 경계를 검토한다.

#### 검증

- 긴 대화 100개 메시지 + 5,000자 답변에서 React commit count 측정
- tool_call이 많은 stream에서 렌더 횟수 측정
- builder/AssistantPanel/TestChatPanel의 `onMessagesCommit` 경로 회귀 테스트 유지
- duplicate id crash 회귀 테스트 유지

### 5. Tool UI JSON 계산 lazy/memo

#### 감사 문서 주장

큰 tool result JSON을 render마다 parse/stringify/탐색할 수 있다.

#### 현재 소스 확인

Generic tool UI는 image URL 탐색을 render path에서 수행한다.

- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx:23`
- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx:107`

args/result stringify도 render path에서 수행된다.

- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx:82`
- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx:117`
- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx:127`

Right rail은 JSON string을 parse하고 pretty stringify한다.

- `frontend/src/components/chat/right-rail/tool-result-panel-content.tsx:24`
- `frontend/src/components/chat/right-rail/tool-result-panel-content.tsx:65`
- `frontend/src/components/chat/right-rail/tool-result-panel-content.tsx:69`
- `frontend/src/components/chat/right-rail/tool-result-panel-content.tsx:84`

#### 판단

진행해야 한다. P0는 아니지만 채팅 중 큰 tool result가 들어오는 agent에서는 체감될 수 있다.

#### 구현 방향

- `extractImageUrls(result)`를 `useMemo`로 감싼다.
- `formatToolValue(args/result)`도 `useMemo`로 감싼다.
- collapsed 상태에서는 큰 body 계산을 하지 않도록 `CollapsiblePill`의 `renderBody` 호출 타이밍을 확인한다.
- Right rail은 panel이 열린 뒤에만 parse/pretty stringify한다.
- 큰 JSON은 처음에는 요약만 보여주고 펼칠 때 pretty stringify하는 threshold를 둔다.

#### 검증

- 1MB JSON tool result 표시
- collapsed 상태에서 stringify가 호출되지 않는지 테스트
- right rail open 시에만 pretty stringify가 실행되는지 테스트

## Chat UI 후속 진행 항목

### Right rail conversation reset

#### 현재 소스 확인

Right rail state는 전역 Jotai atom이고 conversationId를 포함하지 않는다.

- `frontend/src/lib/stores/chat-right-rail.ts:24`
- `frontend/src/lib/stores/chat-right-rail.ts:30`

ChatRightRail은 atom state만 보고 열린다.

- `frontend/src/components/chat/right-rail/chat-right-rail.tsx:24`
- `frontend/src/components/chat/right-rail/chat-right-rail.tsx:25`

#### 판단

작고 안전한 개선이다. 성능 핵심은 아니지만 대화 전환 후 이전 대화의 tool result/subagent/outline이 남는 상태 오류를 막는다.

#### 구현 방향

- payload에 `conversationId`를 넣는다.
- `ChatRightRail`에 현재 conversationId prop을 전달한다.
- atom payload의 conversationId가 현재 conversationId와 다르면 숨기거나 reset한다.
- 대화 route 변경 시 atom을 `{ mode: 'none' }`으로 초기화하는 단순 방법도 가능하다.

### SSE queue head index

#### 현재 소스 확인

POST SSE bridge는 callback buffer에서 `shift()`로 이벤트를 꺼낸다.

- `frontend/src/lib/sse/parse-sse.ts:139`
- `frontend/src/lib/sse/parse-sse.ts:201`
- `frontend/src/lib/sse/parse-sse.ts:233`

#### 판단

진행 가능하지만 우선순위는 낮다. 일반 상황에서는 queue가 작지만, 브라우저가 바쁘거나 consumer가 느릴 때 O(n) 비용이 보일 수 있다.

#### 구현 방향

- `let head = 0` pointer를 둔다.
- yield 시 `buffer[head++]` 사용.
- head가 일정 크기를 넘으면 `buffer.splice(0, head)` 또는 slice compact.

## 측정 후 진행 항목

### `content-visibility:auto` 조정

#### 현재 소스 확인

message wrapper는 content visibility와 intrinsic size를 사용한다.

- user message: `frontend/src/components/chat/assistant-thread.tsx:522`
- assistant message: `frontend/src/components/chat/assistant-thread.tsx:574`

#### 판단

지금 제거하지 않는다. 긴 리스트 초기 렌더에 유효한 최적화다. 실제 스크롤 점프, 이미지 로드 후 layout shift, 긴 table/code block에서 문제가 재현될 때 조정한다.

#### 측정 시나리오

- 이미지 5개 이상 포함된 답변
- 긴 table 답변
- 300줄 이상 code block
- 대화 100개 메시지 이상에서 중간 위치 스크롤 후 새 토큰 수신

### Conversation list virtualization/pagination

#### 현재 소스 확인

ConversationList는 전체 list를 받아 client-side filter/sort/map을 수행한다.

- query: `frontend/src/components/chat/conversation-list.tsx:59`
- filter/sort: `frontend/src/components/chat/conversation-list.tsx:73`
- 전체 map render: `frontend/src/components/chat/conversation-list.tsx:245`

#### 판단

현재 채팅 streaming 깜박임 리팩토링의 직접 범위가 아니다. 대화 수가 많아지는 운영 단계에서 서버 pagination 또는 virtualization으로 진행한다.

## 후순위 또는 지금 하지 않을 항목

### ChatPage title/read 구독 정리

#### 현재 소스 확인

ChatPage는 현재 대화 제목과 unread count를 query observer가 아니라 cache snapshot에서 읽는다.

- `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx:68`
- `frontend/src/app/agents/[agentId]/conversations/[conversationId]/page.tsx:82`

#### 판단

지적은 맞다. 하지만 속도/깜박임 핵심 원인은 아니다. title stale, unread badge 정리 지연 같은 correctness/UX 문제로 후순위 처리한다.

### `CollapsiblePill` defaultExpanded 정책

#### 현재 소스 확인

`defaultExpanded`는 초기 state로만 사용된다.

- `frontend/src/components/chat/tool-ui/collapsible-pill.tsx:125`

file tool preview 확장 여부는 helper로 계산된다.

- `frontend/src/components/chat/tool-ui/code-tool-ui.tsx:72`

#### 판단

성능 문제가 아니라 UX 일관성 문제다. running 상태에서 preview가 나중에 생겼는데 열리지 않는 현상이 실제로 문제 될 때 별도 처리한다.

### Dashboard/AppSidebar/DataTable full client-side 처리

#### 현재 소스 확인

Dashboard는 전체 agents를 client-side filter/sort/render한다.

- `frontend/src/app/page.tsx:70`
- `frontend/src/app/page.tsx:250`

AppSidebar도 전체 agents를 받은 뒤 최근 5개만 계산한다.

- `frontend/src/components/layout/app-sidebar.tsx:132`
- `frontend/src/components/layout/app-sidebar.tsx:212`

DataTable은 client-side search/filter/sort/pagination 구조다.

- `frontend/src/components/ui/data-table.tsx:110`
- `frontend/src/components/ui/data-table.tsx:157`

#### 판단

전역 확장성 문제로는 유효하다. 하지만 현재 요청인 채팅 UI 깜박임/streaming 성능과 분리한다.

## 이미 해결 또는 모니터링 항목

### `content_delta`별 즉시 React state update

#### 현재 소스 확인

`content_delta`는 바로 `setStreamingMessages`를 호출하지 않고 rAF로 묶는다.

- batching 설명: `frontend/src/lib/chat/use-chat-runtime.ts:397`
- rAF schedule: `frontend/src/lib/chat/use-chat-runtime.ts:407`
- content delta 처리: `frontend/src/lib/chat/use-chat-runtime.ts:426`

#### 판단

이미 개선된 항목이다. 추가 수정이 아니라 React Profiler로 효과를 확인하고 회귀 테스트를 유지한다.

## 별도 Backend/API/DB 트랙

### `/api/agents` 집계와 인덱스

#### 현재 소스 확인

`list_agents`는 전체 `conversations`를 `agent_id` 기준으로 group by한 뒤 user agent와 join한다.

- `backend/app/services/agent_service.py:49`
- `backend/app/services/agent_service.py:60`
- user filter는 join 이후: `backend/app/services/agent_service.py:62`

`Conversation.agent_id`에는 모델 레벨 index 선언이 없다.

- `backend/app/models/conversation.py:16`

`Agent.user_id`, `Agent.model_id`에도 모델 레벨 index 선언이 없다.

- `backend/app/models/agent.py:23`
- `backend/app/models/agent.py:29`

마이그레이션 검색에서도 conversation list 정렬에 맞는 명시 인덱스는 확인되지 않았다.

#### 판단

유효하다. 단, 채팅 UI 리팩토링이 아니라 DB/API 성능 PR로 분리한다.

#### 구현 방향

- `conversations(agent_id, is_pinned, updated_at DESC)` 추가 검토
- `/api/agents`에서 user-owned agents를 먼저 좁히고 해당 agent들의 conversations만 집계
- `agents(user_id, updated_at DESC)` 또는 last-used 정렬용 denormalized column 검토
- `agents(model_id)`는 model별 agent count/delete guard에 유효

### Message read path timestamp write

#### 현재 소스 확인

`list_messages_from_checkpointer`는 timestamp가 없는 메시지를 조회 중 `Conversation.message_timestamps` JSON에 기록하고 commit한다.

- timestamp map copy: `backend/app/services/chat_service.py:189`
- missing timestamp 감지: `backend/app/services/chat_service.py:197`
- update/commit: `backend/app/services/chat_service.py:206`

#### 판단

유효하다. 단순 메시지 조회가 write transaction이 되므로 DB 부하와 lock 가능성이 있다. 하지만 timestamp projection 설계가 필요해 별도 PR이 맞다.

#### 구현 방향

- message append/finalize 시점에 timestamp를 저장한다.
- 또는 message projection table을 두고 read path에서는 projection만 읽는다.
- 기존 `message_timestamps` JSON migration/backfill 전략이 필요하다.

### Checkpoint 중복 순회

#### 현재 소스 확인

일반 메시지 list는 leaf checkpoint 중심으로 개선되어 있다.

- `_collect_leaf_checkpoints`: `backend/app/services/thread_branch_service.py:684`
- `build_message_tree`: `backend/app/services/thread_branch_service.py:707`
- list route에서 tree를 한 번 만들어 재사용: `backend/app/routers/conversations.py:620`

하지만 edit/regenerate 경로에는 중복 순회가 남아 있다.

- edit는 `_resolve_branch_checkpoint`에서 `_collect_checkpoints`를 호출할 수 있다: `backend/app/routers/conversations.py:993`
- `_resolve_branch_checkpoint`는 `rewind_to_checkpoint_before_message`를 다시 호출한다: `backend/app/routers/conversations.py:1008`
- `rewind_to_checkpoint_before_message` 내부도 `_collect_checkpoints`를 호출한다: `backend/app/services/thread_branch_service.py:750`
- regenerate는 먼저 `_collect_checkpoints`를 수행한다: `backend/app/routers/conversations.py:1113`
- regenerate 이후 `rewind_to_checkpoint_before_message`에서 다시 수집한다: `backend/app/routers/conversations.py:1162`

#### 판단

유효하다. LangGraph time travel/fork 경로와 관련되므로 별도 테스트가 필요하다.

#### LangGraph 관점

LangGraph persistence에서 checkpointer와 `thread_id`는 대화 기억, HITL, branch/time travel의 핵심이다. 병목 해결 방향은 checkpointer 제거가 아니라 UI projection/cache와 checkpoint 조회 최소화다.

### SSE trace JSON row rewrite

#### 현재 소스 확인

`message_events`는 한 turn당 하나의 row에 `events` JSON 배열을 저장한다.

- `backend/app/models/message_event.py:30`
- `backend/app/models/message_event.py:40`

partial flush 시 기존 row를 읽고 기존 id set을 만든 뒤 새 배열로 병합한다.

- existing row select: `backend/app/services/trace_storage.py:95`
- existing ids build: `backend/app/services/trace_storage.py:112`
- merged events assignment: `backend/app/services/trace_storage.py:128`

#### 판단

유효하다. 긴 응답이나 도구 호출 많은 turn에서 write amplification이 커질 수 있다. schema 변경 가능성이 높아 별도 PR로 진행한다.

#### 구현 방향

- `message_event_chunks` 테이블 추가
- 또는 event-per-row 구조
- resume replay는 chunk pagination으로 처리
- public share/debug trace read path migration 필요

## 별도 Runtime/Deep Agents 트랙

### DeepAgent/runtime 재빌드

#### 현재 소스 확인

한 run마다 model, tools, backend, graph를 새로 구성한다.

- `_prepare_agent`: `backend/app/agent_runtime/executor.py:687`
- model build: `backend/app/agent_runtime/executor.py:700`
- regular tool build: `backend/app/agent_runtime/executor.py:707`
- MCP tool build: `backend/app/agent_runtime/executor.py:719`
- FilesystemBackend 생성: `backend/app/agent_runtime/executor.py:732`
- `create_deep_agent` 호출: `backend/app/agent_runtime/executor.py:808`
- config에 `thread_id` 전달: `backend/app/agent_runtime/executor.py:823`

#### 판단

유효하다. 하지만 cache key, invalidation, user/tenant/credential isolation이 필요한 큰 설계다. 채팅 UI 리팩토링과 분리한다.

#### LangChain/Deep Agents 관점

현재 제품 요구는 다음을 포함한다.

- 장기 대화
- HITL interrupt/resume
- branch/regenerate/time travel
- tools/MCP/skills/filesystem
- schedule trigger

따라서 framework 선택은 Deep Agents + LangGraph가 맞다. 단순 LangChain `create_agent`로 낮추는 것은 권장하지 않는다.

권장되는 최적화 방향은 다음과 같다.

- compiled runtime을 agent config hash 기준으로 cache
- request별 `thread_id`, user context, hook context는 절대 공유하지 않음
- credential version, tool config version, skill version이 cache key 또는 invalidation에 포함
- checkpointer는 유지

### Deep Agents Backend

#### 현재 소스 확인

FastAPI runtime에서 `FilesystemBackend(root_dir=data, virtual_mode=True)`를 기본 backend로 사용한다.

- import: `backend/app/agent_runtime/executor.py:22`
- backend 생성: `backend/app/agent_runtime/executor.py:732`
- create_deep_agent 전달: `backend/app/agent_runtime/executor.py:815`

#### 판단

주의가 필요하다. Deep Agents memory/backend 지침상 web server에서는 기본 file backend보다 StateBackend/StoreBackend/sandbox/CompositeBackend가 더 적합하다.

다만 현재 코드에는 filesystem permission과 virtual mode, skill runtime mount가 얽혀 있으므로 즉시 교체하지 않는다. 별도 설계에서 다룬다.

권장 방향:

- 기본 작업 파일은 StateBackend
- 장기 memory는 StoreBackend
- skill package와 runtime files만 CompositeBackend route로 FilesystemBackend 연결
- 코드 실행이 필요한 경우 sandbox backend 검토

### MCP tool loading

#### 현재 소스 확인

MCP tool config는 `build_tools_config`에서 executor shape로 만들어진다.

- `backend/app/services/chat_service.py:603`
- `backend/app/services/chat_service.py:625`

executor는 서버별로 순차 연결한다.

- server loop: `backend/app/agent_runtime/executor.py:507`
- `MultiServerMCPClient(...).get_tools()`: `backend/app/agent_runtime/executor.py:509`
- timeout: `backend/app/agent_runtime/executor.py:513`

#### 판단

유효하다. 단, MCP session/client를 무조건 장기 cache하는 것은 위험하다. credential/user/server lifecycle을 확인한 뒤 schema cache와 concurrency 제한부터 적용하는 것이 좋다.

## LangChain 관련 비권장 항목

다음은 성능 개선처럼 보일 수 있지만 제품 기능을 깨뜨릴 가능성이 높아 하지 않는다.

| 제안 | 비권장 이유 |
| --- | --- |
| main chat에서 checkpointer 제거 | 대화 기억, HITL resume, branch/time travel이 깨진다. |
| `thread_id` 없이 invoke/stream | thread-scoped persistence가 사라진다. |
| HITL 경로에서 checkpointer 비활성화 | interrupt/resume state를 복원할 수 없다. |
| Deep Agents를 단순 LangChain agent로 하향 | skills/filesystem/subagent/HITL 요구와 맞지 않는다. |
| MCP client/session 무기한 공유 | credential isolation, close lifecycle, schema 변경 감지가 필요하다. |

## 권장 구현 순서

### PR 1: Stream race 안정화

목표:

- stale stream cleanup이 새 stream 상태를 덮지 못하게 한다.
- rAF callback과 `finally`에 token guard를 추가한다.
- rAF cancel을 구현한다.

주요 파일:

- `frontend/src/lib/chat/use-chat-runtime.ts`
- `frontend/src/lib/chat/__tests__/use-chat-runtime-commit.test.tsx`
- 필요 시 새 race test 파일

검증:

- stop/new/edit/regenerate race 단위 테스트
- 기존 HITL tests
- 기존 commit dedup tests

### PR 2: Refetch cleanup effect화

목표:

- render phase state update를 제거한다.
- refetch 완료 후 streaming cleanup 규칙을 effect 안으로 옮긴다.
- partial stream 보존 규칙을 유지한다.

주요 파일:

- `frontend/src/lib/chat/use-chat-runtime.ts`
- `frontend/src/lib/chat/__tests__/has-new-assistant-message.test.ts`
- `frontend/src/lib/chat/__tests__/use-chat-runtime-commit.test.tsx`

검증:

- normal stream 종료 후 backend message 도착 시 streaming clear
- mid-stream 끊김 시 partial assistant 유지
- optimistic user 중복 제거

### PR 3: Streaming markdown/code 경량화

목표:

- streaming 중 fenced code block을 plain render한다.
- message complete 후 syntax highlighting을 적용한다.

주요 파일:

- `frontend/src/components/chat/markdown-content.tsx`
- `frontend/src/components/chat/__tests__/markdown-content.test.tsx`

검증:

- streaming code block plain render
- final code block highlighted render
- mermaid 기존 동작 유지

### PR 4: Streaming projection/usage 비용 축소

목표:

- flush마다 token usage 전체 합산을 피한다.
- 동일 token usage atom update를 줄인다.
- 메시지 merge 임시 배열 생성을 줄인다.

주요 파일:

- `frontend/src/lib/chat/use-chat-runtime.ts`
- `frontend/src/lib/stores/chat-store.ts`

검증:

- token bar 값 유지
- refresh 후 token/cost 유지
- streaming 중 commit count 감소 측정

### PR 5: Tool UI lazy/memo

목표:

- 큰 result parse/stringify를 expanded/open 상태로 미룬다.
- right rail pretty JSON 계산을 memoize한다.

주요 파일:

- `frontend/src/components/chat/tool-ui/generic-tool-ui.tsx`
- `frontend/src/components/chat/right-rail/tool-result-panel-content.tsx`
- `frontend/src/components/chat/tool-ui/collapsible-pill.tsx`

검증:

- collapsed tool card에서 큰 JSON stringify 미실행
- right rail open 시 정상 pretty render
- image URL extraction 유지

## 성능 측정 계획

### 브라우저 시나리오

1. 5,000자 이상 markdown 답변
2. 300줄 이상 code block 답변
3. 큰 JSON tool result
4. 이미지 5개 이상 포함된 답변
5. 응답 중 stop 후 즉시 새 메시지
6. regenerate 후 즉시 stop
7. edit 후 바로 regenerate
8. 네트워크 중단 후 resume 중 새 메시지

### 측정 지표

- React commit count
- main thread long task count
- first token 이후 평균 frame time
- stream 중 input latency
- scroll jump 발생 여부
- stale stream에서 잘못된 refetch 발생 여부
- message complete 전후 깜박임 여부

### 권장 도구

- React Profiler
- Chrome Performance panel
- Playwright race scenario
- Vitest hook tests

## 최종 결정

이번 채팅 UI 속도/깜박임 리팩토링은 프론트 hot path를 먼저 안정화한다.

포함:

- stream stale guard
- rAF cancel
- render phase update 제거
- streaming code plain render
- token usage/projection 비용 축소
- Tool UI lazy/memo

분리:

- DB index
- `/api/agents` brief/list 경량화
- message projection table
- trace chunk/event table
- DeepAgent runtime cache
- Deep Agents backend 교체
- MCP schema/client cache

하지 않음:

- checkpointer 제거
- `thread_id` 생략
- Deep Agents를 단순 LangChain agent로 하향
- 재현 없이 `content-visibility:auto` 제거
