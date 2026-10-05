import { act, renderHook, waitFor } from '@testing-library/react'
import { fromThreadMessageLike } from '@assistant-ui/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { feedbackApi } from '@/lib/api/feedback'
import { resolveFeedbackMessageId, useChatFeedbackAdapter } from '../feedback-adapter'

vi.mock('@/lib/api/feedback', () => ({
  feedbackApi: { set: vi.fn().mockResolvedValue({}), clear: vi.fn().mockResolvedValue(undefined) },
}))

function assistantMessage() {
  const message = fromThreadMessageLike(
    {
      role: 'assistant',
      id: 'runtime-answer',
      content: 'answer',
    },
    'runtime-answer',
    { type: 'complete', reason: 'stop' },
  )
  if (message.role !== 'assistant') throw new Error('Expected assistant fixture')
  return message
}

describe('feedback identity', () => {
  beforeEach(() => vi.clearAllMocks())

  it.each(['runtime-answer', 'runtime-answer::moldy-turn-2', 'public-answer'])(
    'resolves %s to the public feedback target',
    (id) => {
      const ids = new Map([
        ['runtime-answer', 'public-answer'],
        ['public-answer', 'public-answer'],
      ])
      expect(resolveFeedbackMessageId(ids, id)).toBe('public-answer')
      expect(resolveFeedbackMessageId(ids, 'unknown::moldy-turn-2')).toBe('unknown::moldy-turn-2')
    },
  )

  it.each([undefined, 'up'] as const)(
    'persists and toggles ratings using the public id (prior %s)',
    async (prior) => {
      const onMutate = vi.fn()
      const getActiveRating = vi.fn(() => prior)
      const { result } = renderHook(() =>
        useChatFeedbackAdapter('conversation', getActiveRating, {
          resolveMessageId: () => 'public-answer',
          onMutate,
        }),
      )
      act(() => result.current?.submit({ message: assistantMessage(), type: 'positive' }))
      await waitFor(() => expect(onMutate).toHaveBeenCalledOnce())
      expect(getActiveRating).toHaveBeenCalledWith('public-answer')
      if (prior === 'up') {
        expect(feedbackApi.clear).toHaveBeenCalledExactlyOnceWith('public-answer')
        expect(feedbackApi.set).not.toHaveBeenCalled()
      } else {
        expect(feedbackApi.set).toHaveBeenCalledExactlyOnceWith(
          'public-answer',
          'up',
          'conversation',
        )
        expect(feedbackApi.clear).not.toHaveBeenCalled()
      }
    },
  )
})
