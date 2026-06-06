# Chat Scroll Follow Streaming UX Plan

작성일: 2026-06-05

## 목적

긴 deepagent 실행 중 채팅 뷰포트가 사용자의 읽기 위치를 빼앗지 않도록, LambChat의 스크롤 팔로우 정책을 Moldy의 실제 assistant-ui 기반 채팅 구조에 맞게 차용한다.

핵심 목표는 다음 세 가지다.

1. 사용자가 바닥에 머물러 있으면 스트리밍 응답, 도구 결과, 레이아웃 높이 변화가 계속 바닥을 따라간다.
2. 사용자가 위로 스크롤해 과거 메시지나 도구 로그를 읽기 시작하면 자동 추적을 즉시 해제한다.
3. 사용자가 아래 화살표 버튼을 누르거나 새 메시지를 보내면 추적을 명시적으로 재개한다.

## 이 문서만 보고 개발하는 방법

이 문서는 이전 대화 맥락 없이도 구현할 수 있도록 작성한다. 구현자는 아래 순서대로 읽고 작업하면 된다.

1. "현재 Moldy 동작 진단"에서 현재 코드의 출발점을 확인한다.
2. "차용할 정책"에서 제품 동작의 상태 모델과 전이 규칙을 확인한다.
3. "구현 설계"와 "단계별 작업 계획"에 맞춰 파일을 추가하거나 수정한다.
4. "구현 상세 계약"의 함수 signature와 알고리즘을 기준으로 코드를 작성한다.
5. "테스트 상세 계약"의 테스트 이름과 arrange/act/assert를 기준으로 Vitest를 작성한다.
6. "수용 기준"과 "수동 QA"를 통과하면 작업 완료로 본다.

대상 repository는 `/Users/chester/dev/ref/natural-mold`이며, frontend 작업 디렉터리는 `/Users/chester/dev/ref/natural-mold/frontend`다.

## 확인한 소스

### Moldy

| 파일 | 현재 역할 |
| --- | --- |
| `frontend/src/components/chat/assistant-thread.tsx` | 공용 채팅 Thread UI. `ThreadPrimitive.Viewport`를 렌더하고, `onScroll`에서 `isThreadViewportAtBottom`만 계산해 아래 화살표 버튼 표시 여부를 관리한다. |
| `frontend/src/components/chat/scroll-bottom.ts` | `scrollHeight`, `scrollTop`, `clientHeight` 기준으로 "바닥인가"만 판단하는 작은 유틸. |
| `frontend/src/components/chat/__tests__/scroll-bottom.test.ts` | 1px rounding, overflow 없음, 바닥/비바닥만 검증한다. |
| `frontend/src/lib/chat/use-chat-runtime.ts` | SSE 스트림을 assistant-ui `ExternalStoreRuntime` 메시지로 변환한다. `content_delta`는 rAF로 배치되어 스트리밍 메시지 내용을 갱신한다. |
| `frontend/src/lib/chat/convert-message.ts` | backend `Message`를 assistant-ui 메시지로 변환한다. `stream-` prefix assistant 메시지는 `metadata.custom.isStreamingMessage = true`로 표시한다. |
| `frontend/package.json` | `@assistant-ui/react`는 `^0.12.24`. 현재 설치 링크는 `@assistant-ui/react@0.12.28` 기준으로 확인했다. |

### LambChat

| 파일 | 차용할 포인트 |
| --- | --- |
| `/Users/chester/dev/ref/LambChat/frontend/src/components/layout/AppContent/useMessageScroll.followState.ts` | 스크롤 팔로우를 순수 상태 전이로 분리한다. `userScrolledUp`, `autoScrollActive`, `streamLockActive`, `manualDetachFromStream`이 핵심이다. |
| `/Users/chester/dev/ref/LambChat/frontend/src/components/layout/AppContent/messageScrollUtils.ts` | 바닥/근접/이탈 threshold, user scroll 판정, 반복 bottom scroll, streaming finish 판정이 분리되어 있다. |
| `/Users/chester/dev/ref/LambChat/frontend/src/components/layout/AppContent/useMessageScroll.hook.ts` | wheel/touch/scroll/resize/layout 변화에 따라 순수 상태 전이를 실제 DOM 스크롤과 연결한다. |
| `/Users/chester/dev/ref/LambChat/frontend/src/components/layout/AppContent/__tests__/useMessageScroll.test.ts` | 모바일 detach, 데스크톱 detach, stream finish settle, explicit scrollToBottom re-entry 등 체감 UX를 테스트로 고정한다. |

## 현재 Moldy 동작 진단

### 현재 구현

`AssistantThread`는 다음 흐름만 갖고 있다.

```tsx
const [isViewportAtBottom, setIsViewportAtBottom] = useState(true)

const handleViewportScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
  const nextIsAtBottom = isThreadViewportAtBottom(event.currentTarget)
  setIsViewportAtBottom((current) => (current === nextIsAtBottom ? current : nextIsAtBottom))
}, [])
```

그리고 `ThreadPrimitive.Viewport`에 이 핸들러를 연결한다.

```tsx
<ThreadPrimitive.Viewport
  className="min-h-0 flex-1 overflow-y-auto"
  onScroll={handleViewportScroll}
>
```

아래 화살표 버튼은 `useThreadViewport((v) => v.scrollToBottom)`만 호출한다.

```tsx
function ScrollToBottomButton({ isAtBottom }: { isAtBottom: boolean }) {
  const scrollToBottom = useThreadViewport((v) => v.scrollToBottom)
  // ...
  onClick={() => scrollToBottom()}
}
```

`scroll-bottom.ts`는 1px 이내면 바닥으로 취급한다.

```ts
return Math.abs(scrollHeight - scrollTop - clientHeight) <= 1 || scrollHeight <= clientHeight
```

### assistant-ui 기본 동작

현재 `ThreadPrimitive.Viewport`는 props를 별도로 넘기지 않으므로 assistant-ui 기본 auto-scroll이 켜진다.

설치된 `@assistant-ui/react@0.12.28` 기준으로 `ThreadPrimitive.Viewport`는 내부에서 `useThreadViewportAutoScroll`을 사용한다.

- `turnAnchor`가 `"bottom"`이면 `autoScroll` 기본값은 `true`다.
- content resize 시 `autoScroll && isAtBottom`이면 `scrollToBottom("instant")`를 호출한다.
- run start, initialize, thread switch 때도 기본적으로 바닥으로 스크롤한다.
- viewport store에는 `isAtBottom`, `scrollToBottom`, `onScrollToBottom`이 있다.

즉 Moldy는 assistant-ui의 기본 자동 스크롤 위에 "버튼 표시용 바닥 여부"만 별도로 얹고 있다. 사용자가 위로 스크롤했을 때 assistant-ui의 `isAtBottom`이 false가 되면 기본적으로 content resize auto-scroll은 멈추지만, Moldy 코드 차원에서 다음 정책은 아직 고정되어 있지 않다.

- 사용자의 upward wheel/touch를 "자동 추적 해제 의도"로 명시적으로 다루지 않는다.
- 프로그램이 만든 scroll과 사용자가 만든 scroll을 구분하지 않는다.
- 모바일 touch/visual viewport 변화 중 `manualDetachFromStream` 같은 잠금이 없다.
- streaming assistant가 끝날 때 바닥 근처라면 마지막 settle scroll을 할지 결정하지 않는다.
- 위 행동을 검증하는 테스트가 없다.

## 사용자 문제 시나리오

### 1. 긴 deepagent 로그를 읽는 중 화면이 바닥으로 끌리는 느낌

deepagent는 tool call, tool result, approval UI, phase timeline, markdown 텍스트가 길게 이어진다. 사용자가 중간 도구 결과를 읽으려고 위로 올렸는데 새 토큰이나 tool result 높이 변화 때문에 화면이 계속 움직이면 UX가 크게 나빠진다.

현재 assistant-ui 기본값만으로도 일부 detach는 되지만, Moldy의 제품 정책으로 테스트에 고정되어 있지 않아 향후 `ThreadPrimitive.Viewport` props 변경, assistant-ui 버전 변경, 버튼 상태 변경, builder variant 변경 때 회귀하기 쉽다.

### 2. 모바일에서 터치만 시작해도 읽기 의도가 생긴다

LambChat은 모바일에서 active stream 중 `touchstart` 또는 명시적인 upward gesture가 들어오면 `manualDetachFromStream`을 켠다. 이유는 모바일에서 키보드, safe area, visual viewport resize가 빈번하고, 사용자가 손을 댄 직후 passive bottom scroll이 다시 추적을 켜면 "내가 화면을 잡았는데 도로 끌려간다"는 느낌이 생기기 때문이다.

Moldy는 모바일 builder/chat 화면 모두 같은 `AssistantThread`를 쓰므로, 이 정책을 공용으로 가져오는 편이 좋다.

### 3. 새 메시지 전송은 항상 새 추적 사이클이다

사용자가 이전 응답에서 detach 상태였더라도 새 메시지를 보내면 최신 turn으로 이동해야 한다. LambChat 테스트의 `local send clears the detach lock and starts a fresh follow cycle` 케이스가 여기에 해당한다.

Moldy에서는 `useChatRuntime.onNew`, `onResumeDecisions`, `sendMessage`, edit/regenerate가 모두 새 stream을 시작할 수 있다. UI 쪽에서는 assistant-ui `thread.messages`의 마지막 appended user message 또는 `thread.isRunning` 전환을 보고 bottom scroll을 시작할 수 있다.

## 차용할 정책

### 상태 모델

LambChat의 상태 이름을 거의 그대로 차용하되 파일/타입명은 Moldy 맥락에 맞춘다.

```ts
export interface ThreadScrollFollowState {
  userScrolledUp: boolean
  autoScrollActive: boolean
  streamLockActive: boolean
  manualDetachFromStream: boolean
}
```

각 필드 의미는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `userScrolledUp` | 사용자가 현재 응답을 따라가지 않고 위쪽 내용을 읽는 중이다. message update auto-scroll을 막는다. |
| `autoScrollActive` | 현재 bottom scroll loop가 동작 중이다. layout height 변화가 있어도 바닥을 따라가야 한다. |
| `streamLockActive` | assistant stream이 active인 동안 bottom follow를 유지해야 한다. 긴 streaming 중 높이 변화가 계속 생기는 상황을 위한 잠금이다. |
| `manualDetachFromStream` | 특히 모바일에서 사용자가 active stream을 수동 detach했다. passive resize나 near-bottom 판정으로 자동 재결합하면 안 된다. explicit scrollToBottom 또는 새 local send로만 해제한다. |

### 핵심 전이

| 이벤트 | 전이 |
| --- | --- |
| viewport가 바닥 도달 | `userScrolledUp=false`. 단, `manualDetachFromStream`은 해제하지 않는다. |
| explicit scrollToBottom 클릭 | `userScrolledUp=false`, `autoScrollActive=true`, active stream이면 `streamLockActive=true`, `manualDetachFromStream=false`. |
| 새 user message append | detach lock을 해제하고 바닥으로 이동한다. |
| active stream 중 upward wheel/touch/scroll | `userScrolledUp=true`, `autoScrollActive=false`, `streamLockActive=false`. 모바일이면 `manualDetachFromStream=true`. |
| streaming assistant finish + 바닥 근처 + detach 아님 | 마지막 레이아웃 settle을 위해 `request-scroll-to-bottom`. |
| streaming assistant finish + detach 상태 | 아무 scroll도 하지 않는다. |
| viewport resize/layout change | detach가 아니고 follow active 또는 near bottom일 때만 bottom scroll. |

## 구현 설계

### 파일 구성

권장 파일 구성은 다음과 같다.

| 파일 | 작업 |
| --- | --- |
| `frontend/src/components/chat/scroll-bottom.ts` | 기존 `isThreadViewportAtBottom` 유지. distance/near-bottom/away-from-bottom helper를 추가한다. |
| `frontend/src/components/chat/scroll-follow-state.ts` | LambChat의 `useMessageScroll.followState.ts`에 해당하는 순수 상태 전이 유틸을 추가한다. |
| `frontend/src/components/chat/use-thread-scroll-follow.ts` | assistant-ui viewport, `useAuiState`, DOM event, ResizeObserver를 연결하는 React hook을 추가한다. |
| `frontend/src/components/chat/assistant-thread.tsx` | 기존 `isViewportAtBottom` state와 `handleViewportScroll`을 hook 결과로 대체한다. `ThreadPrimitive.Viewport` props를 controlled policy에 맞춘다. |
| `frontend/src/components/chat/__tests__/scroll-follow-state.test.ts` | 순수 전이 테스트를 추가한다. LambChat 테스트 중 Moldy에 맞는 케이스를 이식한다. |
| `frontend/tests/components/chat/assistant-thread-scroll-follow.test.tsx` | assistant-ui mock 기반 component integration 테스트를 추가한다. |

### `scroll-bottom.ts` 확장

현재 함수는 유지한다. 기존 테스트가 깨지지 않아야 한다.

추가 helper는 다음 정도면 충분하다.

```ts
export function getThreadViewportDistanceFromBottom(metrics: ThreadViewportScrollMetrics): number {
  return Math.max(0, metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight)
}

export function isThreadViewportNearBottom(
  metrics: ThreadViewportScrollMetrics,
  thresholdPx: number,
): boolean {
  return metrics.scrollHeight <= metrics.clientHeight ||
    getThreadViewportDistanceFromBottom(metrics) <= thresholdPx
}

export function isThreadViewportAwayFromBottom(
  metrics: ThreadViewportScrollMetrics,
  thresholdPx: number,
): boolean {
  return getThreadViewportDistanceFromBottom(metrics) > thresholdPx
}
```

권장 threshold:

| 항목 | Desktop | Mobile | 이유 |
| --- | --- | --- | --- |
| exact bottom | 1px | 1px | 현재 동작 유지. 버튼 표시에는 엄격한 바닥 판정이 적합하다. |
| near bottom | 48px | 120px | 모바일은 safe area/keyboard/손가락 스크롤 오차가 크다. LambChat의 `getAutoScrollResumeThresholdPx`와 유사하다. |
| away from bottom | 16px | 50px 이상 | 작은 rounding이 아니라 실제로 사용자가 벗어났는지 판단한다. |

### `scroll-follow-state.ts`

순수 유틸은 React와 DOM을 모르게 만든다. 이렇게 해야 LambChat처럼 UX 정책을 작은 테스트로 고정할 수 있다.

주요 export:

```ts
export type ThreadScrollUpdateAction =
  | 'scroll-to-bottom'
  | 'request-scroll-to-bottom'
  | null

export interface ThreadScrollMessageLike {
  id: string
  role?: string
  isStreaming?: boolean
}

export function createThreadScrollFollowState(
  overrides?: Partial<ThreadScrollFollowState>,
): ThreadScrollFollowState

export function getNextThreadScrollFollowStateForAtBottomChange(...)
export function getNextThreadScrollFollowStateForBottomScroll(...)
export function getNextThreadScrollFollowStateForUserIntent(...)
export function getNextThreadScrollFollowStateForUserGesture(...)
export function getNextThreadScrollFollowStateForUserScroll(...)

export function getThreadMessageUpdateScrollAction(...)
export function didLatestStreamingAssistantFinish(...)
export function hasNewOutgoingMessage(...)
export function shouldStopAutoScrollOnUserScroll(...)
```

Moldy에 맞춘 차이:

- LambChat은 `isLoadingHistory`, `pendingHistoryScroll`, external navigation까지 함께 다룬다. Moldy 1차 범위에서는 제외한다.
- LambChat은 Virtuoso를 사용하지만 Moldy는 assistant-ui viewport div를 사용한다. 순수 상태 전이는 동일하게 가져오고, scroll runner는 별도로 작성한다.
- Moldy의 streaming 여부는 assistant-ui `message.status.type === 'running'`을 우선 사용하고, `metadata.custom.isStreamingMessage`는 보조 신호로 쓴다.

### `use-thread-scroll-follow.ts`

hook은 `AssistantThread` 안에서 호출한다.

권장 interface:

```ts
interface UseThreadScrollFollowOptions {
  sessionKey?: string | null
}

interface UseThreadScrollFollowReturn {
  viewportRef: RefCallback<HTMLDivElement>
  isViewportAtBottom: boolean
  handleViewportScroll: (event: UIEvent<HTMLDivElement>) => void
  handleViewportWheel: (event: WheelEvent<HTMLDivElement>) => void
  handleViewportTouchStart: (event: TouchEvent<HTMLDivElement>) => void
  handleViewportTouchMove: (event: TouchEvent<HTMLDivElement>) => void
  handleViewportTouchEnd: () => void
  scrollToBottom: () => void
}
```

구현에서 사용할 assistant-ui state:

```ts
const threadMessages = useAuiState((s) =>
  s.thread.messages.map((message) => ({
    id: message.id,
    role: message.role,
    isStreaming:
      message.role === 'assistant' &&
      (
        message.status?.type === 'running' ||
        message.metadata?.custom?.isStreamingMessage === true
      ),
  })),
)

const threadIsRunning = useAuiState((s) => s.thread.isRunning)
const requestAssistantUiScrollToBottom = useThreadViewport((v) => v.scrollToBottom)
```

`sessionKey`는 `conversationId ?? '__local_thread__'`를 기본으로 쓴다. conversation 전환 시 follow state를 reset한다.

### assistant-ui auto-scroll 제어

커스텀 정책이 들어가면 assistant-ui 기본 content resize auto-scroll과 충돌하지 않도록 `ThreadPrimitive.Viewport`를 controlled하게 둔다.

권장 props:

```tsx
<ThreadPrimitive.Viewport
  ref={viewportRef}
  className="min-h-0 flex-1 overflow-y-auto"
  autoScroll={false}
  scrollToBottomOnRunStart={false}
  onScroll={handleViewportScroll}
  onWheel={handleViewportWheel}
  onTouchStart={handleViewportTouchStart}
  onTouchMove={handleViewportTouchMove}
  onTouchEnd={handleViewportTouchEnd}
  onTouchCancel={handleViewportTouchEnd}
>
```

초기 history load와 thread switch는 1차 구현에서 다음 중 하나를 선택한다.

1. `scrollToBottomOnInitialize`와 `scrollToBottomOnThreadSwitch`는 assistant-ui 기본값으로 유지한다.
2. 모든 자동 옵션을 끄고 `sessionKey` 변경 effect에서 직접 bottom scroll한다.

권장안은 2번이다. 이유는 scroll follow 정책이 한 곳에 모이고, session reset 테스트를 작성하기 쉽기 때문이다.

```tsx
scrollToBottomOnInitialize={false}
scrollToBottomOnThreadSwitch={false}
```

단, 이 경우 빈 상태/초기 로드/대화 전환에서 바닥 이동이 실제로 유지되는지 component test와 수동 검증이 필요하다.

### bottom scroll runner

단순히 한 번 `scrollToBottom()`을 호출하면 streaming markdown, tool result, 이미지/코드 블록, approval card 높이 변화에 밀릴 수 있다. LambChat처럼 짧은 반복 runner를 둔다.

Moldy용 runner는 Virtuoso API가 필요 없다. viewport div와 assistant-ui `scrollToBottom`만 사용한다.

```ts
function forceThreadViewportToBottom(viewport: HTMLElement | null) {
  if (!viewport) return
  viewport.scrollTop = viewport.scrollHeight
}
```

runner 정책:

- 시작 즉시 assistant-ui `scrollToBottom({ behavior: 'auto' })`와 direct `scrollTop = scrollHeight`를 모두 실행한다.
- `ignoreProgrammaticScrollUntilRef.current = Date.now() + 120`으로 다음 scroll event를 사용자 scroll로 오판하지 않는다.
- interval은 desktop 16ms, mobile 20ms 전후.
- 기본 max duration은 240-500ms, stream lock active면 height change가 있는 동안 keep-alive한다.
- `shouldAbort`가 `userScrolledUpRef.current === true`이면 즉시 중단한다.
- ResizeObserver가 가능하면 viewport의 첫 번째 content child를 observe한다. 없으면 interval만 사용한다.

### 버튼 동작

현재 `ScrollToBottomButton`은 내부에서 `useThreadViewport`를 직접 읽는다. controlled hook을 쓰면 버튼은 명령만 받아야 한다.

변경 전:

```tsx
<ScrollToBottomButton isAtBottom={isViewportAtBottom} />
```

변경 후:

```tsx
<ScrollToBottomButton
  isAtBottom={isViewportAtBottom}
  onScrollToBottom={scrollToBottom}
/>
```

`scrollToBottom`은 `manualDetachFromStream`을 clear하는 explicit action이어야 한다. passive resize나 near-bottom 판정은 이 lock을 clear하면 안 된다.

## 단계별 작업 계획

### Phase 1. 순수 유틸과 테스트

작업:

- `scroll-bottom.ts`에 distance/near/away helper 추가.
- `scroll-follow-state.ts` 추가.
- LambChat 테스트 중 다음 케이스를 Moldy 스타일의 Vitest로 이식.

필수 테스트:

| 테스트 | 기대 |
| --- | --- |
| at bottom change clears `userScrolledUp` | 바닥 도달 시 사용자가 위로 올렸다는 flag만 clear한다. |
| mobile upward scroll detaches active stream | `manualDetachFromStream=true`, `autoScrollActive=false`, `streamLockActive=false`. |
| mobile touchstart detaches immediately | active stream follow 중 touch 시작만으로 detach. |
| desktop upward wheel detaches without manual mobile lock | `userScrolledUp=true`, `manualDetachFromStream=false`. |
| detached stream finish does not scroll | `getThreadMessageUpdateScrollAction`이 `null`. |
| stream finish near bottom settles | detach가 아니고 stream lock 유지 중이면 `request-scroll-to-bottom`. |
| explicit scrollToBottom clears detach | `manualDetachFromStream=false`, follow 재개. |
| local send clears detach | 새 user message append는 `scroll-to-bottom`. |
| passive bottom scroll does not clear mobile detach lock | `clearManualDetachFromStream=false`이면 lock 유지. |

검증 명령:

```bash
cd frontend
pnpm vitest run src/components/chat/__tests__/scroll-bottom.test.ts src/components/chat/__tests__/scroll-follow-state.test.ts
```

### Phase 2. Hook 통합

작업:

- `use-thread-scroll-follow.ts` 추가.
- `AssistantThread`의 local `isViewportAtBottom` state와 `handleViewportScroll`을 hook으로 교체.
- `ThreadPrimitive.Viewport`에 `ref`, `autoScroll={false}`, scroll/touch/wheel 핸들러 연결.
- `ScrollToBottomButton`에 `onScrollToBottom` prop 추가.
- `conversationId`를 `sessionKey`로 넘겨 대화 전환 reset.

주의:

- `useAuiState` selector에서 `s.thread.messages` 전체 객체를 그대로 반환하면 token마다 불필요한 rerender가 커질 수 있다. `id`, `role`, `status.type`, `metadata.custom.isStreamingMessage`만 projection한다.
- `scrollToBottom` 호출은 rAF로 한 번 늦춰 DOM이 새 메시지를 그린 뒤 실행한다.
- 프로그램 scroll 직후 발생한 `scroll` event는 `ignoreProgrammaticScrollUntilRef`로 무시한다.

### Phase 3. Component integration test

기존 테스트 mock은 `ThreadPrimitive.Viewport`가 `className`과 `children`만 받는 passthrough라 scroll event 검증에 부족하다. 새 테스트 파일에서는 별도 mock을 만들거나 기존 mock을 보강한다.

검증할 항목:

| 테스트 | 기대 |
| --- | --- |
| viewport receives controlled auto-scroll props | `autoScroll=false`, `scrollToBottomOnRunStart=false`. |
| not-at-bottom shows button | `scrollHeight/clientHeight/scrollTop` 조작 후 button visible. |
| clicking button calls hook scroll action | mock `scrollToBottom` 또는 direct viewport scroll 호출 확인. |
| upward wheel while running keeps button visible and prevents auto re-entry | state가 detached로 유지. |
| sessionKey change resets bottom state | conversationId 변경 후 bottom state 초기화. |

검증 명령:

```bash
cd frontend
pnpm vitest run tests/components/chat/assistant-thread-scroll-follow.test.tsx
```

### Phase 4. 수동 QA와 Playwright 후보

수동 QA 시나리오:

1. 일반 conversation 화면에서 긴 응답을 시작한다.
2. 응답 도중 위로 스크롤해 이전 tool result를 읽는다.
3. 새 토큰/도구 결과가 계속 도착해도 화면이 아래로 끌려가지 않는지 확인한다.
4. 아래 화살표 버튼을 누르면 최신 응답 하단으로 이동하고 이후 다시 따라가는지 확인한다.
5. 같은 시나리오를 builder variant에서 반복한다.
6. 모바일 viewport에서 touchstart/touchmove 후 자동 재결합이 일어나지 않는지 확인한다.
7. 새 메시지를 보내면 이전 detach와 관계없이 최신 turn으로 이동하는지 확인한다.

Playwright 자동화 후보:

- mock SSE endpoint가 긴 `content_delta`를 일정 간격으로 내보내도록 구성.
- 사용자가 viewport를 위로 스크롤한 후 `scrollTop`이 임의로 증가하지 않는지 assert.
- button click 후 `scrollTop + clientHeight`가 `scrollHeight` 근처가 되는지 assert.

## 수용 기준

기능 수용 기준:

- 사용자가 active stream 중 위로 스크롤하면 이후 streaming text/tool UI height 변화가 viewport를 바닥으로 끌고 가지 않는다.
- 사용자가 아래 화살표를 누르면 detach lock이 해제되고 active stream follow가 재개된다.
- 새 user message, edit, regenerate, HITL resume으로 새 run이 시작되면 fresh follow cycle이 시작된다.
- 모바일에서는 touchstart/touchmove로 active stream detach가 가능하고 passive viewport resize로 lock이 해제되지 않는다.
- 기존 아래 화살표 버튼의 접근성 속성(`aria-label`, `aria-hidden`, `tabIndex`, disabled)은 유지된다.

테스트 수용 기준:

- `scroll-bottom.test.ts` 기존 테스트 통과.
- `scroll-follow-state.test.ts`가 LambChat에서 차용한 핵심 상태 전이를 커버.
- `assistant-thread` component test가 controlled viewport props와 button 재결합 경로를 커버.
- `pnpm vitest run` 전체 또는 최소 채팅 관련 테스트 통과.

## 비범위

이번 작업에서 하지 않는 것:

- Virtuoso 도입. Moldy는 assistant-ui viewport를 유지한다.
- backend SSE protocol 변경.
- message virtualization 도입.
- LambChat의 external navigation/reveal_file anchor scroll 이식.
- history pagination 최종 스크롤 정책. Moldy에 history loading UX가 별도 구현되면 후속 문서로 다룬다.
- nested subagent anchor navigation. 다만 ResizeObserver 기반 bottom runner가 nested tool/subagent panel의 높이 변화도 일반 layout change로 처리해야 한다.

## 리스크와 대응

| 리스크 | 대응 |
| --- | --- |
| assistant-ui 버전 변경으로 `useAuiState((s) => s.thread.messages)` shape가 바뀔 수 있음 | 현재 설치본 `@assistant-ui/react@0.12.28` 기준으로 작성했다. 구현 시 타입 에러를 우선 확인하고 selector projection을 최소화한다. |
| assistant-ui 기본 auto-scroll을 끄면 초기 로드/대화 전환 bottom 이동이 빠질 수 있음 | `sessionKey` reset effect와 component test로 보완한다. 필요하면 initialize/threadSwitch만 assistant-ui 기본값을 유지하는 fallback을 둔다. |
| token마다 messages projection이 바뀌어 rerender가 늘 수 있음 | selector에서 필요한 primitive만 반환하고, scroll action 판단은 `previousMessagesRef`와 cheap snapshot 비교로 처리한다. |
| direct `scrollTop = scrollHeight`와 assistant-ui `scrollToBottom` 중복 호출이 튈 수 있음 | programmatic scroll ignore window를 두고, behavior는 기본 `auto`로 통일한다. |
| 모바일 touchstart detach가 너무 민감할 수 있음 | active stream follow 상태에서만 touchstart detach를 적용한다. stream이 없거나 follow가 꺼져 있으면 상태 변화 없음. |
| near-bottom threshold가 버튼 표시와 충돌할 수 있음 | 버튼 표시는 기존 exact bottom 기준을 유지하고, auto-scroll 재개/settle 판단만 near-bottom threshold를 사용한다. |

## 권장 구현 순서 요약

1. `scroll-follow-state.ts`를 만들고 LambChat의 순수 상태 전이 테스트를 Moldy/Vitest로 먼저 고정한다.
2. `scroll-bottom.ts`를 확장하되 기존 exact bottom 동작은 유지한다.
3. `use-thread-scroll-follow.ts`에서 assistant-ui `useAuiState`, `useThreadViewport`, viewport ref, wheel/touch/scroll event를 연결한다.
4. `AssistantThread`에 hook을 붙이고 `ThreadPrimitive.Viewport`를 controlled auto-scroll로 전환한다.
5. 버튼 클릭이 explicit re-entry가 되도록 `ScrollToBottomButton`을 prop 기반으로 바꾼다.
6. component test와 수동 QA로 default/builder/mobile 흐름을 확인한다.

## 구현 상세 계약

이 절은 실제 구현자가 문서만 보고 코드를 작성할 수 있도록 파일별 계약을 정의한다. 코드 블록은 완성 코드에 가깝지만, import 정렬과 타입 좁히기는 실제 TypeScript 에러에 맞춰 조정한다.

### 1. `scroll-bottom.ts`

기존 `isThreadViewportAtBottom` 함수는 이름과 의미를 그대로 유지한다. 아래 helper를 같은 파일에 추가한다.

```ts
export interface ThreadViewportScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

export function isThreadViewportAtBottom({
  scrollHeight,
  scrollTop,
  clientHeight,
}: ThreadViewportScrollMetrics): boolean {
  return Math.abs(scrollHeight - scrollTop - clientHeight) <= 1 || scrollHeight <= clientHeight
}

export function getThreadViewportDistanceFromBottom({
  scrollHeight,
  scrollTop,
  clientHeight,
}: ThreadViewportScrollMetrics): number {
  return Math.max(0, scrollHeight - scrollTop - clientHeight)
}

export function isThreadViewportNearBottom(
  metrics: ThreadViewportScrollMetrics,
  thresholdPx: number,
): boolean {
  return (
    metrics.scrollHeight <= metrics.clientHeight ||
    getThreadViewportDistanceFromBottom(metrics) <= thresholdPx
  )
}

export function isThreadViewportAwayFromBottom(
  metrics: ThreadViewportScrollMetrics,
  thresholdPx: number,
): boolean {
  return getThreadViewportDistanceFromBottom(metrics) > thresholdPx
}
```

추가 테스트는 `frontend/src/components/chat/__tests__/scroll-bottom.test.ts`에 넣는다.

- `getThreadViewportDistanceFromBottom`이 일반 overflow에서 남은 px를 반환한다.
- content가 viewport보다 작으면 near bottom이다.
- threshold 안쪽이면 near bottom이다.
- threshold 바깥이면 away from bottom이다.

### 2. `scroll-follow-state.ts`

새 파일: `frontend/src/components/chat/scroll-follow-state.ts`

이 파일은 React import가 없어야 한다. 상태 전이는 순수 함수로 유지한다.

```ts
export type ThreadScrollUpdateAction = 'scroll-to-bottom' | 'request-scroll-to-bottom' | null

export interface ThreadScrollMessageLike {
  id: string
  role?: string
  isStreaming?: boolean
}

export interface ThreadScrollFollowState {
  userScrolledUp: boolean
  autoScrollActive: boolean
  streamLockActive: boolean
  manualDetachFromStream: boolean
}

export function createThreadScrollFollowState(
  overrides: Partial<ThreadScrollFollowState> = {},
): ThreadScrollFollowState {
  return {
    userScrolledUp: false,
    autoScrollActive: false,
    streamLockActive: false,
    manualDetachFromStream: false,
    ...overrides,
  }
}
```

bottom 도달 전이:

```ts
export function getNextThreadScrollFollowStateForAtBottomChange({
  state,
  atBottom,
}: {
  state: ThreadScrollFollowState
  atBottom: boolean
}): ThreadScrollFollowState {
  if (!atBottom) return state
  return {
    ...state,
    userScrolledUp: false,
  }
}
```

명시적 또는 passive bottom scroll 전이:

```ts
export function getNextThreadScrollFollowStateForBottomScroll({
  state,
  streamingAssistantActive,
  clearManualDetachFromStream = false,
}: {
  state: ThreadScrollFollowState
  streamingAssistantActive: boolean
  clearManualDetachFromStream?: boolean
}): ThreadScrollFollowState {
  if (state.manualDetachFromStream && !clearManualDetachFromStream) {
    return state
  }

  return {
    ...state,
    userScrolledUp: false,
    autoScrollActive: true,
    streamLockActive: streamingAssistantActive,
    manualDetachFromStream: clearManualDetachFromStream
      ? false
      : state.manualDetachFromStream,
  }
}
```

사용자 의도와 gesture 전이:

```ts
function hasActiveStreamFollow({
  state,
  streamingAssistantActive,
}: {
  state: ThreadScrollFollowState
  streamingAssistantActive: boolean
}): boolean {
  return state.autoScrollActive || (state.streamLockActive && streamingAssistantActive)
}

export function getNextThreadScrollFollowStateForUserIntent({
  state,
  isMobileViewport,
  streamingAssistantActive,
}: {
  state: ThreadScrollFollowState
  isMobileViewport: boolean
  streamingAssistantActive: boolean
}): ThreadScrollFollowState {
  if (!hasActiveStreamFollow({ state, streamingAssistantActive })) return state

  return {
    ...state,
    userScrolledUp: true,
    autoScrollActive: false,
    streamLockActive: false,
    manualDetachFromStream:
      state.manualDetachFromStream || (isMobileViewport && streamingAssistantActive),
  }
}

export const getNextThreadScrollFollowStateForUserGesture =
  getNextThreadScrollFollowStateForUserIntent
```

사용자 scroll 전이:

```ts
export function shouldStopAutoScrollOnUserScroll({
  autoScrollActive,
  programmaticScroll,
  movedUp,
  isAwayFromBottom,
  deltaScrollPx,
}: {
  isMobileViewport: boolean
  autoScrollActive: boolean
  programmaticScroll: boolean
  movedUp: boolean
  isAwayFromBottom: boolean
  deltaScrollPx: number
  scrollTop: number
}): boolean {
  if (!autoScrollActive || programmaticScroll || !movedUp) return false
  if (isAwayFromBottom) return true
  return deltaScrollPx > 6
}

export function getNextThreadScrollFollowStateForUserScroll({
  state,
  isMobileViewport,
  streamingAssistantActive,
  programmaticScroll,
  movedUp,
  isAwayFromBottom,
  deltaScrollPx,
  scrollTop,
}: {
  state: ThreadScrollFollowState
  isMobileViewport: boolean
  streamingAssistantActive: boolean
  programmaticScroll: boolean
  movedUp: boolean
  isAwayFromBottom: boolean
  deltaScrollPx: number
  scrollTop: number
}): ThreadScrollFollowState {
  const autoScrollActive = hasActiveStreamFollow({ state, streamingAssistantActive })

  if (
    !shouldStopAutoScrollOnUserScroll({
      isMobileViewport,
      autoScrollActive,
      programmaticScroll,
      movedUp,
      isAwayFromBottom,
      deltaScrollPx,
      scrollTop,
    })
  ) {
    return state
  }

  return {
    ...state,
    userScrolledUp: true,
    autoScrollActive: false,
    streamLockActive: false,
    manualDetachFromStream:
      state.manualDetachFromStream || (isMobileViewport && streamingAssistantActive),
  }
}
```

메시지 변화에 따른 scroll action:

```ts
export function hasNewOutgoingMessage(
  previousMessages: ThreadScrollMessageLike[],
  nextMessages: ThreadScrollMessageLike[],
): boolean {
  if (
    nextMessages.length <= previousMessages.length ||
    nextMessages.length - previousMessages.length > 2
  ) {
    return false
  }

  const appendedMessages = nextMessages.slice(previousMessages.length)
  return appendedMessages[0]?.role === 'user'
}

export function didLatestStreamingAssistantFinish({
  previousMessages,
  nextMessages,
}: {
  previousMessages: ThreadScrollMessageLike[]
  nextMessages: ThreadScrollMessageLike[]
}): boolean {
  const previousLatestMessage = previousMessages[previousMessages.length - 1]
  const nextLatestMessage = nextMessages[nextMessages.length - 1]

  return (
    previousLatestMessage?.id === nextLatestMessage?.id &&
    previousLatestMessage?.role === 'assistant' &&
    nextLatestMessage?.role === 'assistant' &&
    previousLatestMessage.isStreaming === true &&
    nextLatestMessage.isStreaming === false
  )
}

function shouldAutoScrollForMessageUpdate({
  previousMessages,
  nextMessages,
  userScrolledUp,
  autoScrollActive,
  isNearBottom,
  isLoadingHistory = false,
  shouldMaintainStreamLock = false,
  manualDetachActive = false,
}: {
  previousMessages: ThreadScrollMessageLike[]
  nextMessages: ThreadScrollMessageLike[]
  userScrolledUp: boolean
  autoScrollActive: boolean
  isNearBottom: boolean
  isLoadingHistory?: boolean
  shouldMaintainStreamLock?: boolean
  manualDetachActive?: boolean
}): boolean {
  if (userScrolledUp || nextMessages.length === 0 || isLoadingHistory) return false
  if (!autoScrollActive && !isNearBottom && !shouldMaintainStreamLock) return false

  const previousLatestMessage = previousMessages[previousMessages.length - 1]
  const nextLatestMessage = nextMessages[nextMessages.length - 1]
  const appendedMessageCount = nextMessages.length - previousMessages.length

  if (nextLatestMessage?.role !== 'assistant') return false

  const latestChanged = nextLatestMessage.id !== previousLatestMessage?.id
  const latestContinued =
    nextLatestMessage.id === previousLatestMessage?.id &&
    previousLatestMessage?.role === 'assistant'

  if (latestChanged) {
    return !manualDetachActive && appendedMessageCount === 1
  }

  if (
    latestContinued &&
    previousLatestMessage?.isStreaming === true &&
    nextLatestMessage.isStreaming === false
  ) {
    return (
      !manualDetachActive &&
      (autoScrollActive || isNearBottom || shouldMaintainStreamLock)
    )
  }

  if (latestContinued) {
    return (
      !manualDetachActive &&
      nextLatestMessage.isStreaming !== false &&
      !autoScrollActive &&
      (isNearBottom || shouldMaintainStreamLock)
    )
  }

  return false
}

export function getThreadMessageUpdateScrollAction({
  previousMessages,
  nextMessages,
  state,
  isNearBottom,
  isLoadingHistory = false,
  shouldMaintainStreamLock,
}: {
  previousMessages: ThreadScrollMessageLike[]
  nextMessages: ThreadScrollMessageLike[]
  state: ThreadScrollFollowState
  isNearBottom: boolean
  isLoadingHistory?: boolean
  shouldMaintainStreamLock?: boolean
}): ThreadScrollUpdateAction {
  if (hasNewOutgoingMessage(previousMessages, nextMessages)) {
    return 'scroll-to-bottom'
  }

  if (
    shouldAutoScrollForMessageUpdate({
      previousMessages,
      nextMessages,
      userScrolledUp: state.userScrolledUp,
      autoScrollActive: state.autoScrollActive,
      isNearBottom,
      isLoadingHistory,
      shouldMaintainStreamLock,
      manualDetachActive: state.manualDetachFromStream,
    })
  ) {
    return 'request-scroll-to-bottom'
  }

  return null
}
```

### 3. `use-thread-scroll-follow.ts`

새 파일: `frontend/src/components/chat/use-thread-scroll-follow.ts`

이 hook은 `AssistantThread`에서만 호출한다. assistant-ui context 안에서 실행되어야 하므로 `ThreadPrimitive.Root` 바깥으로 빼면 안 된다.

필수 import:

```ts
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefCallback,
  type TouchEvent as ReactTouchEvent,
  type UIEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import { useAuiState, useThreadViewport } from '@assistant-ui/react'
import {
  getThreadViewportDistanceFromBottom,
  isThreadViewportAtBottom,
  isThreadViewportAwayFromBottom,
  isThreadViewportNearBottom,
} from '@/components/chat/scroll-bottom'
import {
  createThreadScrollFollowState,
  getNextThreadScrollFollowStateForAtBottomChange,
  getNextThreadScrollFollowStateForBottomScroll,
  getNextThreadScrollFollowStateForUserGesture,
  getNextThreadScrollFollowStateForUserIntent,
  getNextThreadScrollFollowStateForUserScroll,
  getThreadMessageUpdateScrollAction,
  type ThreadScrollFollowState,
  type ThreadScrollMessageLike,
} from '@/components/chat/scroll-follow-state'
```

hook interface:

```ts
export interface UseThreadScrollFollowOptions {
  sessionKey?: string | null
}

export interface UseThreadScrollFollowReturn {
  viewportRef: RefCallback<HTMLDivElement>
  isViewportAtBottom: boolean
  handleViewportScroll: (event: UIEvent<HTMLDivElement>) => void
  handleViewportWheel: (event: ReactWheelEvent<HTMLDivElement>) => void
  handleViewportTouchStart: (event: ReactTouchEvent<HTMLDivElement>) => void
  handleViewportTouchMove: (event: ReactTouchEvent<HTMLDivElement>) => void
  handleViewportTouchEnd: () => void
  scrollToBottom: () => void
}
```

상수:

```ts
const MOBILE_BREAKPOINT_PX = 640
const MOBILE_NEAR_BOTTOM_PX = 120
const DESKTOP_NEAR_BOTTOM_PX = 48
const MOBILE_AWAY_FROM_BOTTOM_PX = 50
const DESKTOP_AWAY_FROM_BOTTOM_PX = 16
const PROGRAMMATIC_SCROLL_IGNORE_MS = 120
```

viewport helper:

```ts
function isMobileViewport(): boolean {
  return typeof window !== 'undefined' && window.innerWidth < MOBILE_BREAKPOINT_PX
}

function getNearBottomThresholdPx(): number {
  return isMobileViewport() ? MOBILE_NEAR_BOTTOM_PX : DESKTOP_NEAR_BOTTOM_PX
}

function getAwayFromBottomThresholdPx(): number {
  return isMobileViewport() ? MOBILE_AWAY_FROM_BOTTOM_PX : DESKTOP_AWAY_FROM_BOTTOM_PX
}
```

assistant-ui message projection:

```ts
const threadMessages = useAuiState((s) =>
  s.thread.messages.map((message) => ({
    id: message.id,
    role: message.role,
    isStreaming:
      message.role === 'assistant' &&
      (message.status?.type === 'running' ||
        message.metadata?.custom?.isStreamingMessage === true),
  })),
)
```

hook 내부 refs:

```ts
const viewportElementRef = useRef<HTMLDivElement | null>(null)
const previousMessagesRef = useRef<ThreadScrollMessageLike[]>([])
const followStateRef = useRef<ThreadScrollFollowState>(createThreadScrollFollowState())
const isNearBottomRef = useRef(true)
const streamingAssistantActiveRef = useRef(false)
const ignoreProgrammaticScrollUntilRef = useRef(0)
const lastScrollTopRef = useRef(0)
const touchStartYRef = useRef<number | null>(null)
const scrollTimerCleanupRef = useRef<(() => void) | null>(null)
const sessionKeyRef = useRef<string | null | undefined>(sessionKey)
const [isViewportAtBottom, setIsViewportAtBottom] = useState(true)
```

ref callback:

```ts
const viewportRef = useCallback<RefCallback<HTMLDivElement>>((node) => {
  viewportElementRef.current = node
  if (node) {
    lastScrollTopRef.current = node.scrollTop
    const atBottom = isThreadViewportAtBottom(node)
    const nearBottom = isThreadViewportNearBottom(node, getNearBottomThresholdPx())
    setIsViewportAtBottom(atBottom)
    isNearBottomRef.current = nearBottom
  }
}, [])
```

state 적용 helper:

```ts
const applyFollowState = useCallback((nextState: ThreadScrollFollowState) => {
  const previousState = followStateRef.current
  followStateRef.current = nextState
  if (previousState.autoScrollActive && !nextState.autoScrollActive) {
    scrollTimerCleanupRef.current?.()
    scrollTimerCleanupRef.current = null
  }
}, [])
```

bottom scroll runner는 1차 구현에서 복잡도를 낮춰도 된다. 최소 계약은 다음과 같다.

```ts
const requestAssistantUiScrollToBottom = useThreadViewport((v) => v.scrollToBottom)

const forceViewportToBottom = useCallback(() => {
  const viewport = viewportElementRef.current
  requestAssistantUiScrollToBottom({ behavior: 'auto' })
  if (viewport) {
    viewport.scrollTop = viewport.scrollHeight
  }
  ignoreProgrammaticScrollUntilRef.current = Date.now() + PROGRAMMATIC_SCROLL_IGNORE_MS
}, [requestAssistantUiScrollToBottom])
```

반복 runner 계약:

```ts
const startScrollToBottomLoop = useCallback(() => {
  scrollTimerCleanupRef.current?.()
  let attempts = 0
  let stopped = false
  const startedAt = Date.now()

  const tick = () => {
    if (stopped) return
    const viewport = viewportElementRef.current
    if (!viewport) return

    if (followStateRef.current.userScrolledUp) {
      stopped = true
      return
    }

    forceViewportToBottom()
    attempts += 1

    const atBottom = isThreadViewportAtBottom(viewport)
    const keepAlive =
      followStateRef.current.streamLockActive && streamingAssistantActiveRef.current
    const exceeded = attempts >= 20 || Date.now() - startedAt > 600

    if (atBottom && !keepAlive) {
      stopped = true
      followStateRef.current = {
        ...followStateRef.current,
        autoScrollActive: false,
      }
      return
    }

    if (exceeded && !keepAlive) {
      stopped = true
      followStateRef.current = {
        ...followStateRef.current,
        autoScrollActive: false,
      }
    }
  }

  const timer = window.setInterval(tick, isMobileViewport() ? 20 : 16)
  tick()

  scrollTimerCleanupRef.current = () => {
    stopped = true
    window.clearInterval(timer)
  }
}, [forceViewportToBottom])
```

explicit bottom action:

```ts
const requestBottomScroll = useCallback(
  ({ clearManualDetachFromStream }: { clearManualDetachFromStream: boolean }) => {
    const nextState = getNextThreadScrollFollowStateForBottomScroll({
      state: followStateRef.current,
      streamingAssistantActive: streamingAssistantActiveRef.current,
      clearManualDetachFromStream,
    })

    if (nextState === followStateRef.current) return

    applyFollowState(nextState)
    startScrollToBottomLoop()
  },
  [applyFollowState, startScrollToBottomLoop],
)

const scrollToBottom = useCallback(() => {
  requestBottomScroll({ clearManualDetachFromStream: true })
}, [requestBottomScroll])
```

scroll handler:

```ts
const handleViewportScroll = useCallback(
  (event: UIEvent<HTMLDivElement>) => {
    const viewport = event.currentTarget
    const now = Date.now()
    const scrollTop = viewport.scrollTop
    const previousScrollTop = lastScrollTopRef.current
    const movedUp = scrollTop < previousScrollTop - 2
    const upwardScrollPx = Math.max(0, previousScrollTop - scrollTop)
    const programmaticScroll = now <= ignoreProgrammaticScrollUntilRef.current
    const atBottom = isThreadViewportAtBottom(viewport)
    const nearBottom = isThreadViewportNearBottom(viewport, getNearBottomThresholdPx())
    const awayFromBottom = isThreadViewportAwayFromBottom(
      viewport,
      getAwayFromBottomThresholdPx(),
    )

    setIsViewportAtBottom((current) => (current === atBottom ? current : atBottom))
    isNearBottomRef.current = nearBottom

    if (atBottom) {
      applyFollowState(
        getNextThreadScrollFollowStateForAtBottomChange({
          state: followStateRef.current,
          atBottom,
        }),
      )
    } else {
      applyFollowState(
        getNextThreadScrollFollowStateForUserScroll({
          state: followStateRef.current,
          isMobileViewport: isMobileViewport(),
          streamingAssistantActive: streamingAssistantActiveRef.current,
          programmaticScroll,
          movedUp,
          isAwayFromBottom: awayFromBottom,
          deltaScrollPx: upwardScrollPx,
          scrollTop,
        }),
      )
    }

    lastScrollTopRef.current = scrollTop
  },
  [applyFollowState],
)
```

wheel/touch handlers:

```ts
const detachFromUserIntent = useCallback(
  (transition: typeof getNextThreadScrollFollowStateForUserIntent) => {
    const nextState = transition({
      state: followStateRef.current,
      isMobileViewport: isMobileViewport(),
      streamingAssistantActive: streamingAssistantActiveRef.current,
    })
    if (nextState === followStateRef.current) return
    ignoreProgrammaticScrollUntilRef.current = 0
    applyFollowState(nextState)
  },
  [applyFollowState],
)

const handleViewportWheel = useCallback(
  (event: ReactWheelEvent<HTMLDivElement>) => {
    if (event.deltaY >= -1) return
    detachFromUserIntent(getNextThreadScrollFollowStateForUserIntent)
  },
  [detachFromUserIntent],
)

const handleViewportTouchStart = useCallback(
  (event: ReactTouchEvent<HTMLDivElement>) => {
    touchStartYRef.current = event.touches[0]?.clientY ?? null
    detachFromUserIntent(getNextThreadScrollFollowStateForUserIntent)
  },
  [detachFromUserIntent],
)

const handleViewportTouchMove = useCallback(
  (event: ReactTouchEvent<HTMLDivElement>) => {
    const touchStartY = touchStartYRef.current
    const currentTouchY = event.touches[0]?.clientY
    if (!isMobileViewport() || touchStartY == null || typeof currentTouchY !== 'number') return

    const upwardGestureDeltaPx = touchStartY - currentTouchY
    if (upwardGestureDeltaPx <= 6) return

    detachFromUserIntent(getNextThreadScrollFollowStateForUserGesture)
    touchStartYRef.current = currentTouchY
  },
  [detachFromUserIntent],
)

const handleViewportTouchEnd = useCallback(() => {
  touchStartYRef.current = null
}, [])
```

message change effect:

```ts
useEffect(() => {
  const previousMessages = previousMessagesRef.current
  const nextMessages = threadMessages
  const latestMessage = nextMessages[nextMessages.length - 1]
  const streamingAssistantActive =
    latestMessage?.role === 'assistant' && latestMessage.isStreaming === true

  streamingAssistantActiveRef.current = streamingAssistantActive
  previousMessagesRef.current = nextMessages

  const action = getThreadMessageUpdateScrollAction({
    previousMessages,
    nextMessages,
    state: followStateRef.current,
    isNearBottom: isNearBottomRef.current,
    shouldMaintainStreamLock:
      followStateRef.current.streamLockActive && streamingAssistantActive,
  })

  if (action === 'scroll-to-bottom') {
    requestBottomScroll({ clearManualDetachFromStream: true })
    return
  }

  if (action === 'request-scroll-to-bottom') {
    requestBottomScroll({ clearManualDetachFromStream: false })
  }
}, [requestBottomScroll, threadMessages])
```

session reset effect:

```ts
useEffect(() => {
  if (sessionKeyRef.current === sessionKey) return
  sessionKeyRef.current = sessionKey

  scrollTimerCleanupRef.current?.()
  scrollTimerCleanupRef.current = null
  followStateRef.current = createThreadScrollFollowState()
  previousMessagesRef.current = threadMessages
  streamingAssistantActiveRef.current = false
  ignoreProgrammaticScrollUntilRef.current = 0
  touchStartYRef.current = null
  isNearBottomRef.current = true
  setIsViewportAtBottom(true)

  requestAnimationFrame(() => {
    requestBottomScroll({ clearManualDetachFromStream: true })
  })
}, [requestBottomScroll, sessionKey, threadMessages])
```

cleanup:

```ts
useEffect(() => {
  return () => {
    scrollTimerCleanupRef.current?.()
    scrollTimerCleanupRef.current = null
  }
}, [])
```

hook return:

```ts
return {
  viewportRef,
  isViewportAtBottom,
  handleViewportScroll,
  handleViewportWheel,
  handleViewportTouchStart,
  handleViewportTouchMove,
  handleViewportTouchEnd,
  scrollToBottom,
}
```

### 4. `assistant-thread.tsx` 변경 계약

수정 파일: `frontend/src/components/chat/assistant-thread.tsx`

현재 import:

```ts
import { useCallback, useMemo, useState, type UIEvent } from 'react'
```

변경 후 `AssistantThread`에서 local scroll state를 제거하면 `useState`, `UIEvent`는 다른 곳에서 쓰는지 확인하고 필요 없는 항목을 제거한다. 이 파일에는 `CopyButton`, `BranchPicker` 등에서 `useState`를 계속 쓰므로 `useState` 자체는 유지될 가능성이 높다. `UIEvent`는 제거 가능성이 높다.

`useThreadViewport`는 현재 `ScrollToBottomButton` 안에서만 쓰인다. 버튼이 prop 기반으로 바뀌면 `@assistant-ui/react` import에서 제거한다.

추가 import:

```ts
import { useThreadScrollFollow } from '@/components/chat/use-thread-scroll-follow'
```

기존 코드 제거:

```ts
const [isViewportAtBottom, setIsViewportAtBottom] = useState(true)
const handleViewportScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
  const nextIsAtBottom = isThreadViewportAtBottom(event.currentTarget)
  setIsViewportAtBottom((current) => (current === nextIsAtBottom ? current : nextIsAtBottom))
}, [])
```

대체 코드:

```ts
const {
  viewportRef,
  isViewportAtBottom,
  handleViewportScroll,
  handleViewportWheel,
  handleViewportTouchStart,
  handleViewportTouchMove,
  handleViewportTouchEnd,
  scrollToBottom,
} = useThreadScrollFollow({
  sessionKey: conversationId ?? '__local_thread__',
})
```

`ThreadPrimitive.Viewport` 변경:

```tsx
<ThreadPrimitive.Viewport
  ref={viewportRef}
  className="min-h-0 flex-1 overflow-y-auto"
  autoScroll={false}
  scrollToBottomOnRunStart={false}
  scrollToBottomOnInitialize={false}
  scrollToBottomOnThreadSwitch={false}
  onScroll={handleViewportScroll}
  onWheel={handleViewportWheel}
  onTouchStart={handleViewportTouchStart}
  onTouchMove={handleViewportTouchMove}
  onTouchEnd={handleViewportTouchEnd}
  onTouchCancel={handleViewportTouchEnd}
>
```

버튼 호출 변경:

```tsx
<ScrollToBottomButton
  isAtBottom={isViewportAtBottom}
  onScrollToBottom={scrollToBottom}
/>
```

`ScrollToBottomButton` 변경:

```tsx
function ScrollToBottomButton({
  isAtBottom,
  onScrollToBottom,
}: {
  isAtBottom: boolean
  onScrollToBottom: () => void
}) {
  return (
    <button
      type="button"
      aria-label="Scroll to bottom"
      aria-hidden={isAtBottom}
      disabled={isAtBottom}
      tabIndex={isAtBottom ? -1 : 0}
      className={cn(
        'moldy-floating-icon-button flex size-8 items-center justify-center text-muted-foreground',
        isAtBottom ? 'pointer-events-none opacity-0' : 'pointer-events-auto opacity-100',
      )}
      onClick={onScrollToBottom}
    >
      <ArrowDownIcon className="size-4" />
    </button>
  )
}
```

삭제 import:

```ts
import { isThreadViewportAtBottom } from '@/components/chat/scroll-bottom'
```

`isThreadViewportAtBottom`은 새 hook 안에서 사용한다.

## 테스트 상세 계약

테스트는 세 층으로 나눈다.

1. 작은 수학 helper 테스트: `scroll-bottom.test.ts`
2. 상태 머신 테스트: `scroll-follow-state.test.ts`
3. React 통합 테스트: `assistant-thread-scroll-follow.test.tsx`

### 1. `scroll-bottom.test.ts` 추가 케이스

기존 테스트 아래에 추가한다.

```ts
import {
  getThreadViewportDistanceFromBottom,
  isThreadViewportAwayFromBottom,
  isThreadViewportNearBottom,
} from '../scroll-bottom'

it('returns the remaining distance from bottom', () => {
  expect(
    getThreadViewportDistanceFromBottom({
      scrollHeight: 2000,
      scrollTop: 1200,
      clientHeight: 600,
    }),
  ).toBe(200)
})

it('treats a viewport inside the near-bottom threshold as near bottom', () => {
  expect(
    isThreadViewportNearBottom(
      {
        scrollHeight: 2000,
        scrollTop: 1360,
        clientHeight: 600,
      },
      48,
    ),
  ).toBe(true)
})

it('treats a viewport outside the away threshold as away from bottom', () => {
  expect(
    isThreadViewportAwayFromBottom(
      {
        scrollHeight: 2000,
        scrollTop: 1300,
        clientHeight: 600,
      },
      50,
    ),
  ).toBe(true)
})
```

### 2. `scroll-follow-state.test.ts`

새 파일: `frontend/src/components/chat/__tests__/scroll-follow-state.test.ts`

필수 import:

```ts
import { describe, expect, it } from 'vitest'
import {
  createThreadScrollFollowState,
  didLatestStreamingAssistantFinish,
  getNextThreadScrollFollowStateForAtBottomChange,
  getNextThreadScrollFollowStateForBottomScroll,
  getNextThreadScrollFollowStateForUserGesture,
  getNextThreadScrollFollowStateForUserIntent,
  getNextThreadScrollFollowStateForUserScroll,
  getThreadMessageUpdateScrollAction,
} from '../scroll-follow-state'
```

테스트 1: 바닥 도달 시 user flag 해제.

```ts
it('clears the user-scrolled flag when the viewport reaches bottom', () => {
  expect(
    getNextThreadScrollFollowStateForAtBottomChange({
      state: createThreadScrollFollowState({
        userScrolledUp: true,
        autoScrollActive: true,
        streamLockActive: true,
        manualDetachFromStream: true,
      }),
      atBottom: true,
    }),
  ).toEqual({
    userScrolledUp: false,
    autoScrollActive: true,
    streamLockActive: true,
    manualDetachFromStream: true,
  })
})
```

테스트 2: 모바일 upward scroll detach.

```ts
it('marks the active mobile stream as manually detached on upward scroll', () => {
  const nextState = getNextThreadScrollFollowStateForUserScroll({
    state: createThreadScrollFollowState({
      autoScrollActive: true,
      streamLockActive: true,
    }),
    isMobileViewport: true,
    streamingAssistantActive: true,
    programmaticScroll: false,
    movedUp: true,
    isAwayFromBottom: false,
    deltaScrollPx: 12,
    scrollTop: 260,
  })

  expect(nextState).toMatchObject({
    userScrolledUp: true,
    autoScrollActive: false,
    streamLockActive: false,
    manualDetachFromStream: true,
  })
})
```

테스트 3: 모바일 touchstart detach.

```ts
it('detaches the active mobile stream immediately on touch intent', () => {
  const nextState = getNextThreadScrollFollowStateForUserIntent({
    state: createThreadScrollFollowState({
      autoScrollActive: true,
      streamLockActive: true,
    }),
    isMobileViewport: true,
    streamingAssistantActive: true,
  })

  expect(nextState.manualDetachFromStream).toBe(true)
  expect(nextState.userScrolledUp).toBe(true)
  expect(nextState.autoScrollActive).toBe(false)
  expect(nextState.streamLockActive).toBe(false)
})
```

테스트 4: 데스크톱 wheel detach는 mobile lock 없음.

```ts
it('detaches desktop stream follow without setting the mobile detach lock', () => {
  const nextState = getNextThreadScrollFollowStateForUserGesture({
    state: createThreadScrollFollowState({
      autoScrollActive: true,
      streamLockActive: true,
    }),
    isMobileViewport: false,
    streamingAssistantActive: true,
  })

  expect(nextState.userScrolledUp).toBe(true)
  expect(nextState.manualDetachFromStream).toBe(false)
})
```

테스트 5: mobile detach lock 중에는 near bottom이어도 auto-scroll 재개 없음.

```ts
it('does not re-arm streaming follow while mobile detach lock is active', () => {
  expect(
    getThreadMessageUpdateScrollAction({
      previousMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      nextMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      state: createThreadScrollFollowState({
        userScrolledUp: false,
        manualDetachFromStream: true,
      }),
      isNearBottom: true,
    }),
  ).toBeNull()
})
```

테스트 6: stream finish near bottom settle.

```ts
it('settles the bottom lock when the active stream finishes near the bottom', () => {
  expect(
    getThreadMessageUpdateScrollAction({
      previousMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      nextMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: false }],
      state: createThreadScrollFollowState({
        streamLockActive: true,
      }),
      isNearBottom: true,
      shouldMaintainStreamLock: true,
    }),
  ).toBe('request-scroll-to-bottom')
})
```

테스트 7: detached stream finish는 scroll 없음.

```ts
it('does not settle the bottom lock when a detached stream finishes', () => {
  expect(
    getThreadMessageUpdateScrollAction({
      previousMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      nextMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: false }],
      state: createThreadScrollFollowState({
        userScrolledUp: true,
        manualDetachFromStream: true,
      }),
      isNearBottom: false,
      shouldMaintainStreamLock: false,
    }),
  ).toBeNull()
})
```

테스트 8: explicit scrollToBottom clears detach.

```ts
it('explicit scrollToBottom clears the detach lock and resumes follow', () => {
  const nextState = getNextThreadScrollFollowStateForBottomScroll({
    state: createThreadScrollFollowState({
      userScrolledUp: true,
      manualDetachFromStream: true,
    }),
    streamingAssistantActive: true,
    clearManualDetachFromStream: true,
  })

  expect(nextState).toMatchObject({
    userScrolledUp: false,
    autoScrollActive: true,
    streamLockActive: true,
    manualDetachFromStream: false,
  })
})
```

테스트 9: passive scroll은 detach lock을 clear하지 않는다.

```ts
it('passive bottom scroll does not clear the mobile detach lock', () => {
  const state = createThreadScrollFollowState({
    userScrolledUp: true,
    manualDetachFromStream: true,
  })

  expect(
    getNextThreadScrollFollowStateForBottomScroll({
      state,
      streamingAssistantActive: true,
      clearManualDetachFromStream: false,
    }),
  ).toBe(state)
})
```

테스트 10: 새 user message는 fresh follow cycle.

```ts
it('local send clears the detach lock and starts a fresh follow cycle', () => {
  const detachedState = createThreadScrollFollowState({
    userScrolledUp: true,
    manualDetachFromStream: true,
  })

  expect(
    getThreadMessageUpdateScrollAction({
      previousMessages: [{ id: 'assistant-1', role: 'assistant' }],
      nextMessages: [
        { id: 'assistant-1', role: 'assistant' },
        { id: 'user-2', role: 'user' },
      ],
      state: detachedState,
      isNearBottom: false,
    }),
  ).toBe('scroll-to-bottom')
})
```

테스트 11: latest assistant stream finish detector.

```ts
it('detects when the latest assistant stream finishes', () => {
  expect(
    didLatestStreamingAssistantFinish({
      previousMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      nextMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: false }],
    }),
  ).toBe(true)

  expect(
    didLatestStreamingAssistantFinish({
      previousMessages: [{ id: 'assistant-1', role: 'assistant', isStreaming: true }],
      nextMessages: [{ id: 'assistant-2', role: 'assistant', isStreaming: false }],
    }),
  ).toBe(false)
})
```

### 3. `assistant-thread-scroll-follow.test.tsx`

새 파일: `frontend/tests/components/chat/assistant-thread-scroll-follow.test.tsx`

목표는 실제 assistant-ui 전체를 띄우는 것이 아니라, Moldy의 `AssistantThread`가 새 hook/props/button 계약을 제대로 연결하는지 보는 것이다.

mock strategy:

- `ThreadPrimitive.Viewport` mock은 전달받은 props를 실제 `<div>`에 spread해야 한다.
- `useAuiState` mock은 `thread.messages`, `thread.isRunning`, `message` context를 반환할 수 있어야 한다.
- `useThreadViewport` mock은 `scrollToBottom` spy를 selector에 넘겨야 한다.
- `ThreadPrimitive.Messages` mock은 최소 UserMessage/AssistantMessage를 렌더한다.

테스트 scaffold:

```ts
import type { ReactNode } from 'react'
import { fireEvent, render, screen } from '../../test-utils'
import { describe, expect, it, vi } from 'vitest'

const scrollToBottomSpy = vi.fn()

let mockThreadMessages: Array<{
  id: string
  role: 'user' | 'assistant'
  status?: { type: 'running' | 'complete' }
  metadata?: { custom?: Record<string, unknown> }
}> = []

let mockThreadIsRunning = false

vi.mock('@assistant-ui/react', () => {
  const passthrough = ({ children, className }: { children?: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  )

  return {
    ThreadPrimitive: {
      Root: passthrough,
      Viewport: ({
        children,
        ...props
      }: {
        children?: ReactNode
        [key: string]: unknown
      }) => (
        <div data-testid="thread-viewport" {...props}>
          {children}
        </div>
      ),
      Empty: () => null,
      Messages: ({ components }: { components: { UserMessage: () => ReactNode; AssistantMessage: () => ReactNode } }) => (
        <>
          {components.UserMessage()}
          {components.AssistantMessage()}
        </>
      ),
      ViewportFooter: passthrough,
      If: ({ children }: { children?: ReactNode }) => <>{children}</>,
    },
    MessagePrimitive: {
      Content: () => <span>메시지</span>,
    },
    ComposerPrimitive: {
      Root: passthrough,
      Input: () => <textarea aria-label="message input" />,
      Cancel: ({ children }: { children?: ReactNode }) => <>{children}</>,
      Send: ({ children }: { children?: ReactNode }) => <>{children}</>,
      Attachments: () => null,
      AddAttachment: ({ children }: { children?: ReactNode }) => <>{children}</>,
    },
    AttachmentPrimitive: {
      Root: passthrough,
      Name: () => <span>file.txt</span>,
      Remove: ({ children }: { children?: ReactNode }) => <>{children}</>,
    },
    ActionBarPrimitive: {
      Copy: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
      Edit: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
      Reload: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
      FeedbackPositive: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
      FeedbackNegative: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
    },
    useThreadViewport: (selector: (state: { scrollToBottom: typeof scrollToBottomSpy }) => unknown) =>
      selector({ scrollToBottom: scrollToBottomSpy }),
    useAuiState: (selector: (state: unknown) => unknown) =>
      selector({
        thread: {
          messages: mockThreadMessages,
          isRunning: mockThreadIsRunning,
          isDisabled: false,
          capabilities: { attachments: false, queue: false },
        },
        message: {
          status: { type: 'complete' },
          metadata: { custom: {}, submittedFeedback: undefined },
        },
        composer: { dictation: null, isEditing: true, text: '' },
      }),
    useAssistantState: (selector: (state: unknown) => unknown) =>
      selector({
        message: {
          status: { type: 'complete' },
          metadata: { custom: {}, submittedFeedback: undefined },
        },
      }),
    useAui: () => ({
      composer: () => ({
        addAttachment: vi.fn(),
        getState: () => ({ isEditing: true, isEmpty: true }),
        send: vi.fn(),
        setText: vi.fn(),
      }),
      thread: () => ({
        cancelRun: vi.fn(),
        getState: () => ({ capabilities: { attachments: false, queue: false }, isRunning: false }),
      }),
    }),
    makeAssistantToolUI: () => () => <div data-testid="tool-ui" />,
  }
})
```

테스트 1: viewport controlled props.

```ts
it('renders the viewport with controlled auto-scroll props', () => {
  render(<AssistantThread />)
  const viewport = screen.getByTestId('thread-viewport')

  expect(viewport).toHaveAttribute('autoscroll', 'false')
  expect(viewport).toHaveAttribute('scrolltobottomonrunstart', 'false')
})
```

주의: React가 boolean custom prop을 DOM attribute로 어떻게 내리는지 테스트 환경에서 다를 수 있다. 불안정하면 mock `Viewport` 안에서 props를 별도 spy에 저장해 `expect(lastViewportProps.autoScroll).toBe(false)` 방식으로 검증한다. 이 방식을 권장한다.

테스트 2: not-at-bottom shows button.

```ts
it('shows the scroll-to-bottom button when viewport is away from bottom', () => {
  render(<AssistantThread />)
  const viewport = screen.getByTestId('thread-viewport')

  Object.defineProperties(viewport, {
    scrollHeight: { value: 2000, configurable: true },
    clientHeight: { value: 600, configurable: true },
    scrollTop: { value: 200, configurable: true },
  })

  fireEvent.scroll(viewport)

  const button = screen.getByRole('button', { name: 'Scroll to bottom' })
  expect(button).not.toBeDisabled()
})
```

테스트 3: button click delegates explicit re-entry.

```ts
it('scrolls to bottom when the floating button is clicked', () => {
  render(<AssistantThread />)
  const viewport = screen.getByTestId('thread-viewport')

  Object.defineProperties(viewport, {
    scrollHeight: { value: 2000, configurable: true },
    clientHeight: { value: 600, configurable: true },
    scrollTop: { value: 200, configurable: true },
  })

  fireEvent.scroll(viewport)
  fireEvent.click(screen.getByRole('button', { name: 'Scroll to bottom' }))

  expect(scrollToBottomSpy).toHaveBeenCalled()
})
```

테스트 4: active stream upward wheel detach.

```ts
it('keeps the viewport detached after an upward wheel gesture during streaming', () => {
  mockThreadIsRunning = true
  mockThreadMessages = [
    { id: 'user-1', role: 'user' },
    {
      id: 'assistant-1',
      role: 'assistant',
      status: { type: 'running' },
      metadata: { custom: { isStreamingMessage: true } },
    },
  ]

  render(<AssistantThread />)
  const viewport = screen.getByTestId('thread-viewport')

  Object.defineProperties(viewport, {
    scrollHeight: { value: 2000, configurable: true },
    clientHeight: { value: 600, configurable: true },
    scrollTop: { value: 1000, configurable: true, writable: true },
  })

  fireEvent.wheel(viewport, { deltaY: -20 })

  mockThreadMessages = [
    ...mockThreadMessages.slice(0, -1),
    {
      id: 'assistant-1',
      role: 'assistant',
      status: { type: 'running' },
      metadata: { custom: { isStreamingMessage: true } },
    },
  ]

  expect(scrollToBottomSpy).not.toHaveBeenCalled()
})
```

테스트 4는 구현 방식에 따라 rerender가 필요할 수 있다. `render` 결과의 `rerender(<AssistantThread />)`를 사용해 message update effect를 다시 태운다.

### 4. 실행 명령

최소 검증:

```bash
cd frontend
pnpm vitest run src/components/chat/__tests__/scroll-bottom.test.ts src/components/chat/__tests__/scroll-follow-state.test.ts tests/components/chat/assistant-thread-scroll-follow.test.tsx
```

채팅 관련 회귀:

```bash
cd frontend
pnpm vitest run src/components/chat/__tests__ tests/components/chat src/lib/chat/__tests__
```

전체 frontend 단위 테스트:

```bash
cd frontend
pnpm vitest run
```

## 구현 중 확인해야 할 실제 코드 포인트

작업 전후로 반드시 확인할 파일과 이유:

| 파일 | 확인 이유 |
| --- | --- |
| `frontend/src/components/chat/assistant-thread.tsx` | `useThreadViewport` import 제거 여부, `ScrollToBottomButton` prop 변경, `ThreadPrimitive.Viewport` props 변경. |
| `frontend/tests/components/chat/assistant-thread-actions.test.tsx` | 기존 assistant-ui mock이 `useThreadViewport` state shape를 단순하게 가정한다. 새 hook이 `useAuiState`를 요구하면 mock 보강이 필요할 수 있다. |
| `frontend/tests/components/chat/assistant-thread-edit.test.tsx` | 위와 동일하게 assistant-ui mock 보강 가능성이 있다. |
| `frontend/src/components/chat/builder-overrides.tsx` | builder variant 자체는 `AssistantThread` viewport를 공유하므로 별도 변경은 없어야 한다. 다만 streaming indicator가 동일하게 동작하는지 수동 QA가 필요하다. |
| `frontend/src/lib/chat/convert-message.ts` | streaming assistant marker인 `metadata.custom.isStreamingMessage`가 유지되는지 확인한다. |
| `frontend/src/lib/chat/use-chat-runtime.ts` | `stream-` id assistant message가 streaming 중 유지되고, message_end 후 `isRunning`/status 전환이 hook selector에서 감지되는지 확인한다. |

## Definition Of Done

아래 조건을 모두 만족해야 완료다.

- 새 문서의 Phase 1, Phase 2, Phase 3 작업이 모두 구현되어 있다.
- `ThreadPrimitive.Viewport`는 커스텀 follow hook이 제어한다.
- 사용자가 active stream 중 위로 스크롤하면 이후 content resize와 message update가 bottom scroll을 실행하지 않는다.
- 아래 화살표 버튼 클릭은 `manualDetachFromStream`을 해제하고 follow를 재개한다.
- 새 user message append는 이전 detach 상태와 관계없이 bottom scroll을 시작한다.
- 모바일 touchstart 또는 upward touchmove는 active stream follow를 detach한다.
- `scroll-bottom.test.ts`, `scroll-follow-state.test.ts`, `assistant-thread-scroll-follow.test.tsx`가 통과한다.
- 기존 `assistant-thread-actions.test.tsx`, `assistant-thread-edit.test.tsx`가 새 assistant-ui mock 요구사항에 맞게 통과한다.
- 수동 QA에서 default conversation과 builder variant 모두 같은 스크롤 정책을 보인다.

## 최종 판단

이 항목은 Moldy에 차용할 가치가 높다. 현재 Moldy는 assistant-ui 기본 auto-scroll에 의존하고 있어 기본적인 채팅 경험은 동작하지만, deepagent처럼 긴 스트리밍과 도구 UI height 변화가 많은 제품에서는 "사용자가 읽는 위치를 존중한다"는 정책을 명시적으로 가져가는 편이 안전하다.

LambChat의 구현을 그대로 복사하지 말고, 순수 상태 전이와 테스트 철학을 차용한 뒤 assistant-ui viewport에 맞는 작은 hook으로 통합하는 것이 가장 적합하다.
