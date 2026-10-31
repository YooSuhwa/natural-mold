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
      expect(resolveFeedbackMessageId(ids, 'unknown::moldy-turn-2')).toBeUndefined()
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

  it('skips persisting while the public id is unresolved and nothing can refresh it', () => {
    const { result } = renderHook(() =>
      useChatFeedbackAdapter('conversation', vi.fn(), { resolveMessageId: () => undefined }),
    )
    act(() => result.current?.submit({ message: assistantMessage(), type: 'positive' }))
    expect(feedbackApi.set).not.toHaveBeenCalled()
    expect(feedbackApi.clear).not.toHaveBeenCalled()
  })

  it.each([
    [undefined, 'set'],
    ['up', 'clear'],
  ] as const)(
    'refreshes an unresolved id once and persists against it (refreshed prior %s)',
    async (rating, call) => {
      const onMutate = vi.fn()
      const getActiveRating = vi.fn(() => undefined)
      const refreshMessageId = vi.fn(async () => ({ id: 'public-answer', rating }))
      const { result } = renderHook(() =>
        useChatFeedbackAdapter('conversation', getActiveRating, {
          resolveMessageId: () => undefined,
          refreshMessageId,
          onMutate,
        }),
      )
      act(() => result.current?.submit({ message: assistantMessage(), type: 'positive' }))
      await waitFor(() => expect(onMutate).toHaveBeenCalledOnce())
      expect(refreshMessageId).toHaveBeenCalledExactlyOnceWith('runtime-answer')
      expect(getActiveRating).not.toHaveBeenCalled()
      if (call === 'set') {
        expect(feedbackApi.set).toHaveBeenCalledExactlyOnceWith(
          'public-answer',
          'up',
          'conversation',
        )
        expect(feedbackApi.clear).not.toHaveBeenCalled()
      } else {
        expect(feedbackApi.clear).toHaveBeenCalledExactlyOnceWith('public-answer')
        expect(feedbackApi.set).not.toHaveBeenCalled()
      }
    },
  )

  it('skips persisting when the refreshed messages still lack the id', async () => {
    const refreshMessageId = vi.fn(async () => undefined)
    const { result } = renderHook(() =>
      useChatFeedbackAdapter('conversation', vi.fn(), {
        resolveMessageId: () => undefined,
        refreshMessageId,
      }),
    )
    act(() => result.current?.submit({ message: assistantMessage(), type: 'positive' }))
    await waitFor(() => expect(refreshMessageId).toHaveBeenCalledOnce())
    await Promise.resolve()
    expect(feedbackApi.set).not.toHaveBeenCalled()
    expect(feedbackApi.clear).not.toHaveBeenCalled()
  })
})
