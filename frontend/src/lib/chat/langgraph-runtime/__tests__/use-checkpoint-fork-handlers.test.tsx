import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HumanMessage, type BaseMessage } from '@langchain/core/messages'
import type { MessageMetadataMap, UseStreamReturn } from '@langchain/react'
import type { AppendMessage } from '@assistant-ui/react'
import { useCheckpointForkHandlers } from '../use-checkpoint-fork-handlers'
import type { ServerCheckpointContext } from '../thread-state-checkpoints'

const mocks = vi.hoisted(() => ({
  // useMessageMetadataSnapshot의 useSyncExternalStore가 STREAM_CONTROLLER 심볼로
  // stream에서 메타데이터 스토어를 읽으므로, 동일 심볼을 노출한다.
  STREAM_CONTROLLER: Symbol('STREAM_CONTROLLER'),
  loadServerCheckpointContext:
    vi.fn<(conversationId: string) => Promise<ServerCheckpointContext>>(),
  reportClientWarning: vi.fn(),
}))

const STREAM_CONTROLLER = mocks.STREAM_CONTROLLER

vi.mock('@langchain/react', () => ({
  STREAM_CONTROLLER: mocks.STREAM_CONTROLLER,
}))

vi.mock('../thread-state-checkpoints', () => ({
  loadServerCheckpointContext: mocks.loadServerCheckpointContext,
}))

vi.mock('@/lib/logging/client-logger', () => ({
  reportClientWarning: mocks.reportClientWarning,
}))

interface MutableStream {
  submit: ReturnType<typeof vi.fn>
  [STREAM_CONTROLLER]: {
    messageMetadataStore: {
      subscribe: (onChange: () => void) => () => void
      getSnapshot: () => MessageMetadataMap
    }
  }
}

function createStream(): MutableStream {
  const emptyMetadata: MessageMetadataMap = new Map()
  return {
    submit: vi.fn().mockResolvedValue(undefined),
    [STREAM_CONTROLLER]: {
      messageMetadataStore: {
        subscribe: () => () => {},
        getSnapshot: () => emptyMetadata,
      },
    },
  }
}

/** checkpoint를 찾지 못하고 서버 메시지도 없는 컨텍스트 — retryServerCheckpoint가
 *  계속 폴링하며 abortable sleep으로 진입하게 만든다. */
function emptyServerContext(): ServerCheckpointContext {
  return {
    checkpointByMessageId: new Map(),
    metadataByMessageId: new Map(),
    messageIdsByIndex: [],
  }
}

function renderHandlers(stream: MutableStream) {
  return renderHook(() =>
    useCheckpointForkHandlers({
      conversationId: 'conversation-1',
      stream: stream as unknown as UseStreamReturn<Record<string, unknown>>,
      // 로컬 checkpoint를 찾지 못하게 빈 가시 메시지/메시지 목록을 준다 →
      // 서버 폴링 경로로 떨어진다.
      visibleMessages: [],
      langChainMessages: [] as readonly BaseMessage[],
    }),
  )
}

function editMessage(): AppendMessage {
  return {
    content: [{ type: 'text', text: 'edited prompt' }],
    parentId: 'missing-parent',
    sourceId: 'missing-source',
  } as unknown as AppendMessage
}

describe('useCheckpointForkHandlers abortable server checkpoint polling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mocks.loadServerCheckpointContext.mockReset()
    mocks.reportClientWarning.mockReset()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('unmount 시 poll 루프를 중단하고 dead stream에 submit하지 않는다', async () => {
    // 서버 컨텍스트는 매번 checkpoint 없는 결과를 돌려줘 폴링을 계속하게 한다.
    mocks.loadServerCheckpointContext.mockResolvedValue(emptyServerContext())

    const stream = createStream()
    const { result, unmount } = renderHandlers(stream)

    let editResult: boolean | undefined
    // onEdit는 await로 끝나지 않고 폴링 루프(sleep)에 매달린다.
    act(() => {
      void result.current.onEdit(editMessage()).then((value) => {
        editResult = value
      })
    })

    // 첫 서버 로드(마이크로태스크) 이후 sleep 타이머에 진입할 때까지 진행시킨다.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(mocks.loadServerCheckpointContext).toHaveBeenCalled()
    expect(stream.submit).not.toHaveBeenCalled()

    // unmount → cleanup이 AbortController.abort()를 호출 → sleep 즉시 resolve →
    // signal.aborted 가드에서 루프를 빠져나가고 submit 없이 false를 반환한다.
    await act(async () => {
      unmount()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(stream.submit).not.toHaveBeenCalled()
    expect(editResult).toBe(false)
  })

  it('핸들러 재생성(handler recreate)으로 이전 poll이 취소되어도 submit하지 않는다', async () => {
    mocks.loadServerCheckpointContext.mockResolvedValue(emptyServerContext())

    const stream = createStream()
    const { result } = renderHandlers(stream)

    let firstEditResult: boolean | undefined
    act(() => {
      void result.current.onEdit(editMessage()).then((value) => {
        firstEditResult = value
      })
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // beginServerCheckpointPoll은 새 호출 시 이전 controller를 abort한다.
    // onReload가 새 poll을 시작하면 첫 onEdit poll의 signal이 발화한다.
    act(() => {
      void result.current.onReload('missing-parent')
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // 첫 onEdit는 취소되어 submit 없이 false로 끝난다.
    expect(firstEditResult).toBe(false)
    expect(stream.submit).not.toHaveBeenCalled()
  })

  it('abort된 sleep은 타이머 만료 없이 즉시 resolve되어 루프를 빠져나간다', async () => {
    // sleep(250ms) 진입 후 abort가 오면 setTimeout이 만료되기 전에 resolve해야 한다.
    // 가짜 타이머를 advance하지 않고도 unmount만으로 onEdit가 종료되면 즉시 resolve가
    // 증명된다.
    mocks.loadServerCheckpointContext.mockResolvedValue(emptyServerContext())

    const stream = createStream()
    const { result, unmount } = renderHandlers(stream)

    let settled = false
    act(() => {
      void result.current.onEdit(editMessage()).then(() => {
        settled = true
      })
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(settled).toBe(false)

    // 타이머를 advance하지 않는다(250ms 미경과). abort만으로 resolve되어야 한다.
    await act(async () => {
      unmount()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(settled).toBe(true)
    expect(stream.submit).not.toHaveBeenCalled()
  })
})

describe('useCheckpointForkHandlers retry fork excludes synthetic notice bubbles (G2)', () => {
  it('실패 notice 버블을 건너뛰고 마지막 user checkpoint에서 fork한다', async () => {
    const stream = createStream()
    const userId = 'user-1'
    const failedBubbleId = 'moldy-failed-run-1'
    const langChainMessages = [
      new HumanMessage({
        id: userId,
        content: 'hi',
        additional_kwargs: { metadata: { checkpoint_id: 'ck-user' } },
      }),
    ] as unknown as readonly BaseMessage[]

    const { result } = renderHook(() =>
      useCheckpointForkHandlers({
        conversationId: 'conversation-1',
        stream: stream as unknown as UseStreamReturn<Record<string, unknown>>,
        // user 다음에 합성 실패 버블(assistant role, checkpoint 없음)이 온다.
        visibleMessages: [
          { id: userId, role: 'user' },
          { id: failedBubbleId, role: 'assistant' },
        ],
        langChainMessages,
      }),
    )

    await act(async () => {
      await result.current.onReload(userId)
    })

    // 합성 notice를 필터하지 않으면 checkpointForReload가 그것을 재생성 대상
    // assistant로 오인해 null → no-op(retry 버그)이 된다. 필터 덕에 checkpoint가
    // 있는 마지막 user 턴에서 fork한다.
    expect(stream.submit).toHaveBeenCalledWith(null, { forkFrom: 'ck-user' })
  })
})
