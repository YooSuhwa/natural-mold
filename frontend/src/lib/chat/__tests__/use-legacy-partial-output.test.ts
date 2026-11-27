import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api/client'
import { beginOrgSwitch, finishOrgSwitch } from '@/lib/org-scope/org-scope-signal'
import { dispatchMoldyBranchSwitched } from '../langgraph-runtime/branch-switch-events'
import { useLegacyPartialOutput } from '../use-legacy-partial-output'

vi.mock('@/lib/api/client', () => ({ apiFetch: vi.fn() }))

const chat = '11111111-1111-4111-8111-111111111111'
const otherChat = '22222222-2222-4222-8222-222222222222'
const run = '33333333-3333-4333-8333-333333333333'
const readState = vi.mocked(apiFetch)
const partial = {
  text: '마지막으로 받은 문장',
  tools: [{ name: 'lookup', state: 'started' }],
  truncated: false,
}

function snapshot(
  fields: Record<string, unknown> = {},
  checkpointId = 'branch-A',
  conversationId = chat,
) {
  return {
    metadata: {
      conversation_id: conversationId,
      latest_run: {
        id: run,
        status: 'failed',
        error_code: 'partial_model_stream',
        partial_output: partial,
        ...fields,
      },
    },
    checkpoint: { checkpoint_id: checkpointId, thread_id: conversationId, checkpoint_ns: '' },
  }
}

type Identity = { conversationId?: string; runId?: string }
const initialProps: Identity = { conversationId: chat, runId: run }

function useRead({ conversationId, runId }: Identity) {
  return useLegacyPartialOutput(conversationId, runId)
}

describe('legacy terminal stream recovery', () => {
  beforeEach(() => readState.mockReset())

  it('fetches only for an identified run and conversation and exposes only the proven projection', async () => {
    const reply = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState.mockReturnValueOnce(reply.promise)
    const { result, rerender } = renderHook(useRead, { initialProps: {} as Identity })
    rerender({ conversationId: chat })
    rerender({ runId: run })
    expect(result.current).toBeNull()
    expect(readState).not.toHaveBeenCalled()
    rerender(initialProps)
    expect(result.current).toBeNull()
    expect(readState).toHaveBeenCalledExactlyOnceWith(
      `/api/conversations/${chat}/langgraph/threads/${chat}/state?read_only=true`,
      { signal: expect.any(AbortSignal) },
    )
    await act(async () => reply.resolve(snapshot()))
    expect(result.current).toEqual(partial)
  })

  it.each([
    ['another run', snapshot({ id: 'other-run' })],
    ['an ordinary failure', snapshot({ error_code: 'stream_error' })],
    ['a running model', snapshot({ status: 'running' })],
    ['missing branch evidence', snapshot({ partial_output: null })],
    [
      'an empty role chunk',
      snapshot({ partial_output: { text: '', tools: [], truncated: false } }),
    ],
    ['another conversation', snapshot({}, 'branch-A', otherChat)],
  ])('withholds readback for %s', async (_, state) => {
    const reply = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState.mockReturnValueOnce(reply.promise)
    const { result } = renderHook(useRead, { initialProps })
    await act(async () => reply.resolve(state))
    expect(result.current).toBeNull()
  })

  it('revokes previously loaded output on a branch change, ignores stale replies, and remains empty on access denial', async () => {
    const first = Promise.withResolvers<ReturnType<typeof snapshot>>()
    const second = Promise.withResolvers<ReturnType<typeof snapshot>>()
    const third = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise)
    const { result } = renderHook(useRead, { initialProps })
    await act(async () => first.resolve(snapshot()))
    expect(result.current).toEqual(partial)
    act(() => dispatchMoldyBranchSwitched({ conversationId: otherChat, checkpointId: 'unrelated' }))
    expect(readState).toHaveBeenCalledTimes(1)

    act(() => dispatchMoldyBranchSwitched({ conversationId: chat, checkpointId: 'branch-B' }))
    expect(result.current).toBeNull()
    act(() => dispatchMoldyBranchSwitched({ conversationId: chat, checkpointId: 'branch-C' }))
    await act(async () => second.resolve(snapshot({}, 'branch-B')))
    expect(result.current).toBeNull()
    await act(async () => third.reject(new Error('Forbidden')))
    expect(result.current).toBeNull()
    expect(readState).toHaveBeenCalledTimes(3)
  })

  it('requires readback to match the branch selected during refresh', async () => {
    const readA = Promise.withResolvers<ReturnType<typeof snapshot>>()
    const readB = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState.mockReturnValueOnce(readA.promise).mockReturnValueOnce(readB.promise)
    const { result } = renderHook(useRead, { initialProps })
    act(() => dispatchMoldyBranchSwitched({ conversationId: chat, checkpointId: 'branch-B' }))
    await act(async () => readB.resolve(snapshot({}, 'branch-A')))
    await act(async () => readA.resolve(snapshot({}, 'branch-A')))
    expect(result.current).toBeNull()
  })

  it('clears the verified projection immediately when the active organization changes', async () => {
    const reply = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState.mockReturnValueOnce(reply.promise)
    const { result } = renderHook(useRead, { initialProps })
    await act(async () => reply.resolve(snapshot()))
    expect(result.current).toEqual(partial)
    try {
      act(() => beginOrgSwitch())
      expect(result.current).toBeNull()
      act(() => dispatchMoldyBranchSwitched({ conversationId: chat, checkpointId: 'branch-B' }))
      expect(readState).toHaveBeenCalledTimes(1)
    } finally {
      finishOrgSwitch()
    }
  })

  it('drops late replies after navigation and checks the selected run again on remount', async () => {
    const old = Promise.withResolvers<ReturnType<typeof snapshot>>()
    const reopened = Promise.withResolvers<ReturnType<typeof snapshot>>()
    readState.mockReturnValueOnce(old.promise).mockReturnValueOnce(reopened.promise)
    const first = renderHook(useRead, { initialProps })
    first.rerender({ conversationId: otherChat })
    await act(async () => old.resolve(snapshot()))
    expect(first.result.current).toBeNull()
    first.unmount()
    const second = renderHook(useRead, { initialProps })
    expect(second.result.current).toBeNull()
    await act(async () => reopened.resolve(snapshot()))
    expect(second.result.current).toEqual(partial)
    expect(readState).toHaveBeenCalledTimes(2)
  })
})
