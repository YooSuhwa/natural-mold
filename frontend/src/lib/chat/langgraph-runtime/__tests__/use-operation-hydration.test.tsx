import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { editHydrationReady, useOperationHydration } from '../use-operation-hydration'
import { loadServerThreadState } from '../thread-state-checkpoints'

vi.mock('../thread-state-checkpoints', () => ({
  loadServerThreadState: vi.fn(),
}))

describe('useOperationHydration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('applies a not-ready edit snapshot before scheduling the next hydration attempt', async () => {
    const state = {
      values: {
        messages: [{ type: 'human', id: 'old-user', content: 'old content' }],
      },
      metadata: { latest_run: { id: 'run-1', status: 'completed' } },
    }
    vi.mocked(loadServerThreadState).mockResolvedValue(state)
    const handleThreadState = vi.fn()
    const clear = vi.fn()

    const { unmount } = renderHook(() =>
      useOperationHydration({
        conversationId: 'conversation-edit',
        streamLoading: false,
        operation: {
          conversationId: 'conversation-edit',
          attemptId: 1,
          value: {
            conversationId: 'conversation-edit',
            content: 'edited content',
            parentId: null,
            sourceId: 'old-user',
            targetId: 'old-user',
            targetIndex: 0,
            staleTailFingerprints: [],
            staleTailContentFingerprints: [],
            staleConvertedTailContentFingerprints: [],
            requiresLatestBranchMetadata: false,
            pendingBranchTotal: null,
          },
        },
        isReady: editHydrationReady,
        handleNotReadyState: true,
        reloadRunCorrelation: {
          conversationId: 'conversation-edit',
          pendingReload: false,
          acceptedRunId: null,
        },
        handleThreadState,
        clearServerHydrationState: vi.fn(),
        clear,
        failureCode: 'edit_hydration_failed',
      }),
    )

    await waitFor(() => expect(handleThreadState).toHaveBeenCalledWith(state))
    expect(clear).not.toHaveBeenCalled()
    unmount()
  })
})
