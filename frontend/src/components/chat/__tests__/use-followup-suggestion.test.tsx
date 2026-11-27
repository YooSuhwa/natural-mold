import type { PropsWithChildren } from 'react'
import { AgUiFollowupCompletionContext } from '@/lib/ag-ui/followup-completion-context'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useFollowupSuggestion } from '../use-followup-suggestion'

type FollowupResponse = { suggestion?: string | null }

const mocks = vi.hoisted(() => ({
  isRunning: true,
  enabled: true,
  fetchSuggestion: vi.fn<(conversationId: string) => Promise<FollowupResponse>>(),
  setFollowup: vi.fn(),
  reportClientError: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  useAuiState: (selector: (state: { thread: { isRunning: boolean } }) => unknown) =>
    selector({ thread: { isRunning: mocks.isRunning } }),
}))

vi.mock('jotai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('jotai')>()
  return {
    ...actual,
    useAtomValue: () => mocks.enabled,
    useSetAtom: () => mocks.setFollowup,
  }
})

vi.mock('@/lib/hooks/use-conversations', () => ({
  useFollowupSuggestionMutation: () => ({ mutateAsync: mocks.fetchSuggestion }),
}))

vi.mock('@/lib/logging/client-logger', () => ({
  reportClientError: mocks.reportClientError,
}))

function deferred<T>() {
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((_resolve, rejectPromise) => {
    reject = rejectPromise
  })
  return { promise, reject }
}

describe('useFollowupSuggestion', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.isRunning = true
    mocks.enabled = true
  })

  it('skips GET replay and interrupts, and only suggests once for a locally completed AG-UI turn', async () => {
    let receipt: string | null = null
    const wrapper = ({ children }: PropsWithChildren) => (
      <AgUiFollowupCompletionContext.Provider value={receipt}>
        {children}
      </AgUiFollowupCompletionContext.Provider>
    )
    mocks.fetchSuggestion.mockResolvedValue({ suggestion: 'Keep going' })
    const { rerender } = renderHook(() => useFollowupSuggestion('conversation-1'), { wrapper })
    mocks.isRunning = false
    rerender()
    expect(mocks.fetchSuggestion).not.toHaveBeenCalled()
    mocks.isRunning = true
    rerender()
    receipt = 'successful-local-run'
    rerender()
    expect(mocks.fetchSuggestion).not.toHaveBeenCalled()
    mocks.isRunning = false
    rerender()
    await waitFor(() =>
      expect(mocks.setFollowup).toHaveBeenCalledWith({
        conversationId: 'conversation-1',
        suggestion: 'Keep going',
      }),
    )
    rerender()
    expect(mocks.fetchSuggestion).toHaveBeenCalledTimes(1)
    receipt = null
    mocks.enabled = false
    mocks.isRunning = true
    rerender()
    receipt = 'user-disabled-suggestions'
    mocks.isRunning = false
    rerender()
    mocks.enabled = true
    rerender()
    expect(mocks.fetchSuggestion).toHaveBeenCalledTimes(1)
  })

  it('ignores a rejected suggestion request after cleanup', async () => {
    const pending = deferred<FollowupResponse>()
    mocks.fetchSuggestion.mockReturnValue(pending.promise)
    const { rerender, unmount } = renderHook(() => useFollowupSuggestion('conversation-1'))

    mocks.isRunning = false
    rerender()
    await waitFor(() => {
      expect(mocks.fetchSuggestion).toHaveBeenCalledWith('conversation-1')
    })

    unmount()
    await act(async () => {
      pending.reject(new Error('request aborted during cleanup'))
      await Promise.resolve()
    })

    expect(mocks.reportClientError).not.toHaveBeenCalled()
    expect(mocks.setFollowup).not.toHaveBeenCalled()
  })

  it('ignores a rejected suggestion request after pagehide', async () => {
    const pending = deferred<FollowupResponse>()
    mocks.fetchSuggestion.mockReturnValue(pending.promise)
    const { rerender } = renderHook(() => useFollowupSuggestion('conversation-1'))

    mocks.isRunning = false
    rerender()
    await waitFor(() => {
      expect(mocks.fetchSuggestion).toHaveBeenCalledWith('conversation-1')
    })

    window.dispatchEvent(new Event('pagehide'))
    await act(async () => {
      pending.reject(new Error('request aborted during pagehide'))
      await Promise.resolve()
    })

    expect(mocks.reportClientError).not.toHaveBeenCalled()
    expect(mocks.setFollowup).not.toHaveBeenCalled()
  })

  it('reports a rejected suggestion request while still mounted', async () => {
    const pending = deferred<FollowupResponse>()
    mocks.fetchSuggestion.mockReturnValue(pending.promise)
    const { rerender } = renderHook(() => useFollowupSuggestion('conversation-1'))

    mocks.isRunning = false
    rerender()
    await waitFor(() => {
      expect(mocks.fetchSuggestion).toHaveBeenCalledWith('conversation-1')
    })

    const error = new Error('request failed')
    await act(async () => {
      pending.reject(error)
      await Promise.resolve()
    })

    expect(mocks.reportClientError).toHaveBeenCalledWith(
      'useFollowupSuggestion',
      'suggestion fetch failed:',
      error,
    )
  })
})
