import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useSubmitCheckpointController } from '../use-submit-checkpoint-controller'

describe('useSubmitCheckpointController', () => {
  it('keeps one conversation-keyed singleton store with FIFO eviction at 50 entries', () => {
    const conversationIds = Array.from({ length: 51 }, (_, index) => `submit-controller-${index}`)
    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId: string }) =>
        useSubmitCheckpointController(conversationId),
      { initialProps: { conversationId: conversationIds[0] ?? '' } },
    )

    for (const [index, conversationId] of conversationIds.entries()) {
      rerender({ conversationId })
      act(() => {
        result.current.beginPendingSubmit(`message-${index}`, index)
      })
    }

    rerender({ conversationId: conversationIds[0] ?? '' })
    expect(result.current.pendingSubmit).toBeNull()
    rerender({ conversationId: conversationIds[50] ?? '' })
    expect(result.current.pendingSubmit?.content).toBe('message-50')

    for (const conversationId of conversationIds) {
      rerender({ conversationId })
      act(() => result.current.clearConversationPendingSubmit())
    }
  })
})
