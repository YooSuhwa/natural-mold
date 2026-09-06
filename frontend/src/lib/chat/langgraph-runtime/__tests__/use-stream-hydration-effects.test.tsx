import { act, renderHook } from '@testing-library/react'
import type { BaseMessage } from '@langchain/core/messages'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { dispatchMoldyBranchSwitched } from '../branch-switch-events'
import { useStreamHydrationEffects } from '../use-stream-hydration-effects'

const mocks = vi.hoisted(() => ({
  loadServerThreadState: vi.fn<(conversationId: string) => Promise<unknown>>(),
  reportRuntimeFailure: vi.fn(),
  useOperationHydration: vi.fn(),
}))

vi.mock('../thread-state-checkpoints', () => ({
  loadServerThreadState: mocks.loadServerThreadState,
}))

vi.mock('../runtime-warning', () => ({
  reportRuntimeFailure: mocks.reportRuntimeFailure,
}))

vi.mock('../use-operation-hydration', () => ({
  editHydrationReady: () => false,
  reloadHydrationReady: () => false,
  useOperationHydration: mocks.useOperationHydration,
}))

const CONVERSATION_ID = '11111111-1111-4111-8111-111111111111'

type HydrationProps = Parameters<typeof useStreamHydrationEffects>[0]
type TestHydrationProps = HydrationProps & {
  readonly handleThreadState: ReturnType<typeof vi.fn<HydrationProps['handleThreadState']>>
}

function createProps(
  overrides: Partial<Omit<HydrationProps, 'handleThreadState'>> = {},
): TestHydrationProps {
  return {
    conversationId: CONVERSATION_ID,
    activateTransportHydration: vi.fn(),
    streamLoading: false,
    pendingEdit: null,
    pendingReload: null,
    reloadRunCorrelation: {
      conversationId: CONVERSATION_ID,
      pendingReload: false,
      acceptedRunId: null,
    },
    handleThreadState: vi.fn<HydrationProps['handleThreadState']>(),
    clearServerHydrationState: vi.fn(),
    clearPendingEdit: vi.fn(),
    clearPendingReload: vi.fn(),
    latestVisibleMessagesRef: { current: [] as readonly BaseMessage[] },
    ...overrides,
  }
}

function resolvedState(content = 'ready response'): unknown {
  return {
    values: {
      messages: [
        { type: 'human', id: 'human-1', content: 'question' },
        { type: 'ai', id: 'assistant-1', content },
      ],
    },
  }
}

function createDeferred<T>(): {
  readonly promise: Promise<T>
  readonly resolve: (value: T) => void
} {
  let resolveDeferred: (value: T) => void = () => undefined
  const promise = new Promise<T>((resolve) => {
    resolveDeferred = resolve
  })
  return { promise, resolve: resolveDeferred }
}

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('useStreamHydrationEffects', () => {
  beforeEach(() => {
    mocks.loadServerThreadState.mockReset()
    mocks.reportRuntimeFailure.mockReset()
    mocks.useOperationHydration.mockReset()
  })

  it('skips initial state loading for a non-persisted id while activating transport hydration', async () => {
    const props = createProps({ conversationId: 'draft-conversation' })
    const { unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      {
        initialProps: props,
      },
    )

    await flushMicrotasks()

    expect(mocks.loadServerThreadState).not.toHaveBeenCalled()
    expect(props.activateTransportHydration).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('hydrates the initial persisted state when loading succeeds', async () => {
    const state = resolvedState()
    mocks.loadServerThreadState.mockResolvedValue(state)
    const props = createProps()
    const { unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      {
        initialProps: props,
      },
    )

    await flushMicrotasks()

    expect(mocks.loadServerThreadState).toHaveBeenCalledWith(CONVERSATION_ID)
    expect(props.handleThreadState).toHaveBeenCalledWith(state)
    expect(mocks.reportRuntimeFailure).not.toHaveBeenCalled()
    unmount()
  })

  it('reports a rejected initial persisted state load', async () => {
    const failure = new Error('initial load failed')
    mocks.loadServerThreadState.mockRejectedValue(failure)
    const props = createProps()
    const { unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      {
        initialProps: props,
      },
    )

    await flushMicrotasks()

    expect(props.handleThreadState).not.toHaveBeenCalled()
    expect(mocks.reportRuntimeFailure).toHaveBeenCalledWith(failure, 'initial_hydration_failed')
    unmount()
  })

  it('hydrates a matching branch switch and ignores an unrelated branch switch', async () => {
    const initialState = resolvedState('initial response')
    const branchState = resolvedState('branch response')
    mocks.loadServerThreadState
      .mockResolvedValueOnce(initialState)
      .mockResolvedValueOnce(branchState)
    const props = createProps()
    const { unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      {
        initialProps: props,
      },
    )

    await flushMicrotasks()
    mocks.loadServerThreadState.mockClear()
    props.handleThreadState.mockClear()

    act(() => {
      dispatchMoldyBranchSwitched({
        conversationId: '22222222-2222-4222-8222-222222222222',
        checkpointId: 'other-checkpoint',
      })
    })
    await flushMicrotasks()
    expect(mocks.loadServerThreadState).not.toHaveBeenCalled()

    act(() => {
      dispatchMoldyBranchSwitched({ conversationId: CONVERSATION_ID, checkpointId: 'checkpoint-1' })
    })
    await flushMicrotasks()

    expect(mocks.loadServerThreadState).toHaveBeenCalledWith(CONVERSATION_ID)
    expect(props.handleThreadState).toHaveBeenCalledWith(branchState, { replaceMessages: true })
    unmount()
  })

  it('reports a rejected matching branch switch load', async () => {
    const failure = new Error('branch load failed')
    mocks.loadServerThreadState
      .mockResolvedValueOnce(resolvedState())
      .mockRejectedValueOnce(failure)
    const props = createProps()
    const { unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      {
        initialProps: props,
      },
    )

    await flushMicrotasks()
    mocks.reportRuntimeFailure.mockClear()

    act(() => {
      dispatchMoldyBranchSwitched({ conversationId: CONVERSATION_ID, checkpointId: 'checkpoint-1' })
    })
    await flushMicrotasks()

    expect(mocks.reportRuntimeFailure).toHaveBeenCalledWith(failure, 'branch_hydration_failed')
    unmount()
  })

  it('applies a ready first post-run state with replacement and settles', async () => {
    const state = resolvedState()
    mocks.loadServerThreadState.mockResolvedValue(state)
    const props = createProps({ streamLoading: true })
    const { result, rerender, unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      { initialProps: props },
    )

    rerender({ ...props, streamLoading: false })
    await flushMicrotasks()

    expect(mocks.loadServerThreadState).toHaveBeenCalledTimes(2)
    expect(props.handleThreadState).toHaveBeenCalledWith(state, { replaceMessages: true })
    expect(result.current.postRunHydrationPending).toBe(false)
    unmount()
  })

  it('ignores a late post-run result after cancellation', async () => {
    mocks.loadServerThreadState.mockResolvedValueOnce(resolvedState('initial response'))
    const props = createProps({ streamLoading: true })
    const { result, rerender, unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      { initialProps: props },
    )

    const deferred = createDeferred<unknown>()
    mocks.loadServerThreadState.mockImplementationOnce(() => deferred.promise)
    rerender({ ...props, streamLoading: false })
    await flushMicrotasks()
    expect(mocks.loadServerThreadState).toHaveBeenCalledTimes(2)
    props.handleThreadState.mockClear()
    act(() => result.current.cancelPostRunHydration())
    await act(async () => {
      deferred.resolve(resolvedState('late response'))
      await Promise.resolve()
    })

    expect(props.handleThreadState).not.toHaveBeenCalled()
    expect(result.current.postRunHydrationPending).toBe(false)
    unmount()
  })

  it('ignores a late post-run result after unmount', async () => {
    mocks.loadServerThreadState.mockResolvedValueOnce(resolvedState('initial response'))
    const props = createProps({ streamLoading: true })
    const { rerender, unmount } = renderHook(
      (current: HydrationProps) => useStreamHydrationEffects(current),
      { initialProps: props },
    )

    const deferred = createDeferred<unknown>()
    mocks.loadServerThreadState.mockImplementationOnce(() => deferred.promise)
    rerender({ ...props, streamLoading: false })
    await flushMicrotasks()
    expect(mocks.loadServerThreadState).toHaveBeenCalledTimes(2)
    props.handleThreadState.mockClear()
    unmount()
    await act(async () => {
      deferred.resolve(resolvedState('late response'))
      await Promise.resolve()
    })

    expect(props.handleThreadState).not.toHaveBeenCalled()
  })
})
