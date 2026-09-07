import { renderHook, waitFor } from '@testing-library/react'
import { HumanMessage } from '@langchain/core/messages'
import { describe, expect, it, vi } from 'vitest'

import { EMPTY_SERVER_MESSAGE_METADATA } from '../stream-thread-state-projection'
import { useStreamVisibleMessages } from '../use-stream-visible-messages'
import type { PendingNewSubmitState } from '../use-submit-checkpoint-controller'
import type { Message } from '@/lib/types'

const CONTENT = '사과, 배, 포도 중에 하나 선택하는 ask user 해줘'

function pendingSubmit(): PendingNewSubmitState {
  return {
    conversationId: 'conversation-1',
    attemptId: 1,
    content: CONTENT,
    baseMessageCount: 0,
    message: new HumanMessage({ id: 'moldy-pending-user:conversation-1:1', content: CONTENT }),
  }
}

function persistedUserMessage(): Message {
  return {
    id: 'user-1',
    conversation_id: 'conversation-1',
    role: 'user',
    content: CONTENT,
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-09-07T00:00:00Z',
  }
}

describe('useStreamVisibleMessages', () => {
  it('keeps the pending user bubble through a transient stream acknowledgement regression', async () => {
    const clearPendingSubmit = vi.fn(() => true)
    const initialProps: Parameters<typeof useStreamVisibleMessages>[0] = {
      streamMessages: [],
      isLoading: true,
      postRunHydrationPending: false,
      serverMessages: [],
      serverMessageMetadata: EMPTY_SERVER_MESSAGE_METADATA,
      pendingSubmit: pendingSubmit(),
      clearPendingSubmit,
      pendingEdit: null,
      pendingReload: null,
    }
    const { result, rerender } = renderHook(
      (props: Parameters<typeof useStreamVisibleMessages>[0]) => useStreamVisibleMessages(props),
      { initialProps },
    )

    expect(result.current.messages.map((message) => message.content)).toContain(CONTENT)

    rerender({
      ...initialProps,
      streamMessages: [new HumanMessage({ id: 'stream-user-1', content: CONTENT })],
    })
    expect(clearPendingSubmit).not.toHaveBeenCalled()

    rerender(initialProps)
    expect(result.current.messages.map((message) => message.content)).toContain(CONTENT)
    expect(clearPendingSubmit).not.toHaveBeenCalled()

    rerender({ ...initialProps, serverMessages: [persistedUserMessage()] })
    await waitFor(() => {
      expect(clearPendingSubmit).toHaveBeenCalledExactlyOnceWith(CONTENT, 1)
    })
  })

  it('keeps a persisted user bubble when an interrupt snapshot temporarily regresses', () => {
    const firstPrompt = '안녕?'
    const latestPrompt = CONTENT
    const initialProps: Parameters<typeof useStreamVisibleMessages>[0] = {
      streamMessages: [new HumanMessage({ id: 'stream-user-1', content: firstPrompt })],
      isLoading: true,
      postRunHydrationPending: false,
      serverMessages: [
        {
          ...persistedUserMessage(),
          id: 'user-0',
          content: firstPrompt,
        },
        persistedUserMessage(),
      ],
      serverMessageMetadata: EMPTY_SERVER_MESSAGE_METADATA,
      pendingSubmit: null,
      clearPendingSubmit: vi.fn(() => true),
      pendingEdit: null,
      pendingReload: null,
    }

    const { result } = renderHook(() => useStreamVisibleMessages(initialProps))

    expect(result.current.settling).toBe(true)
    expect(result.current.messages.map((message) => message.content)).toEqual([
      firstPrompt,
      latestPrompt,
    ])
  })
})
