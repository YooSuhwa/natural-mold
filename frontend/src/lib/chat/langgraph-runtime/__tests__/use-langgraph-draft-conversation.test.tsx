import { StrictMode, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLangGraphDraftConversation } from '../use-langgraph-draft-conversation'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  createDraft: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/lib/api/conversations', () => ({
  conversationsApi: {
    create: mocks.create,
    createDraft: mocks.createDraft,
    delete: mocks.delete,
  },
}))

describe('useLangGraphDraftConversation', () => {
  beforeEach(() => {
    mocks.create.mockReset()
    mocks.createDraft.mockReset()
    mocks.delete.mockReset()
    mocks.delete.mockResolvedValue(undefined)
    mocks.create.mockResolvedValue({
      id: 'conversation-v3',
      agent_id: 'agent-1',
      title: null,
      is_pinned: false,
      unread_count: 0,
      last_read_at: null,
      last_unread_at: null,
      last_activity_source: 'user',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    })
    mocks.createDraft.mockResolvedValue({
      id: 'conversation-v3',
      agent_id: 'agent-1',
      title: null,
      is_pinned: false,
      unread_count: 0,
      last_read_at: null,
      last_unread_at: null,
      last_activity_source: 'user',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    })
  })

  it('creates a hidden draft conversation for LangGraph v3 draft threads', async () => {
    const onConversationId = vi.fn()

    const { result } = renderHook(() =>
      useLangGraphDraftConversation({
        agentId: 'agent-1',
        isDraftConversation: true,
        runtimeMode: 'langgraph_v3',
        onConversationId,
      }),
    )

    expect(result.current.isBootstrapping).toBe(true)
    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-v3')
    })
    expect(mocks.createDraft).toHaveBeenCalledWith('agent-1')
    expect(mocks.create).not.toHaveBeenCalled()
    expect(onConversationId).toHaveBeenCalledWith('conversation-v3')
  })

  it('does not create a conversation outside LangGraph v3 draft mode', () => {
    const onConversationId = vi.fn()

    renderHook(() =>
      useLangGraphDraftConversation({
        agentId: 'agent-1',
        isDraftConversation: true,
        runtimeMode: 'legacy',
        onConversationId,
      }),
    )
    renderHook(() =>
      useLangGraphDraftConversation({
        agentId: 'agent-1',
        isDraftConversation: false,
        runtimeMode: 'langgraph_v3',
        onConversationId,
      }),
    )

    expect(mocks.create).not.toHaveBeenCalled()
    expect(mocks.createDraft).not.toHaveBeenCalled()
    expect(onConversationId).not.toHaveBeenCalled()
  })

  it('does not restart draft bootstrap when only the callback changes', async () => {
    let resolveFirst: (conversation: { id: string }) => void = () => undefined
    mocks.createDraft.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve
      }),
    )
    const firstCallback = vi.fn()
    const secondCallback = vi.fn()

    const { result, rerender } = renderHook(
      ({ onConversationId }) =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation: true,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { initialProps: { onConversationId: firstCallback } },
    )

    rerender({ onConversationId: secondCallback })
    resolveFirst({ id: 'cancelled-conversation' })

    await waitFor(() => {
      expect(result.current.conversationId).toBe('cancelled-conversation')
    })
    expect(mocks.createDraft).toHaveBeenCalledTimes(1)
    expect(firstCallback).not.toHaveBeenCalled()
    expect(secondCallback).toHaveBeenCalledWith('cancelled-conversation')
  })

  it('does not create duplicate conversations during StrictMode effect replay', async () => {
    const onConversationId = vi.fn()
    const wrapper = ({ children }: { readonly children: ReactNode }) => (
      <StrictMode>{children}</StrictMode>
    )

    const { result } = renderHook(
      () =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation: true,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-v3')
    })
    expect(mocks.createDraft).toHaveBeenCalledTimes(1)
    expect(onConversationId).toHaveBeenCalledWith('conversation-v3')
  })

  it('returns bootstrap errors instead of throwing during render', async () => {
    const failure = new Error('network unavailable')
    mocks.createDraft.mockRejectedValueOnce(failure)
    const onConversationId = vi.fn()

    const { result } = renderHook(() =>
      useLangGraphDraftConversation({
        agentId: 'agent-1',
        isDraftConversation: true,
        runtimeMode: 'langgraph_v3',
        onConversationId,
      }),
    )

    await waitFor(() => {
      expect(result.current.error).toBe(failure)
    })
    expect(result.current.isBootstrapping).toBe(false)
    expect(result.current.conversationId).toBeNull()
    expect(onConversationId).not.toHaveBeenCalled()
  })

  it('creates a fresh conversation when the same agent re-enters a v3 draft route', async () => {
    mocks.createDraft
      .mockResolvedValueOnce({
        id: 'conversation-first',
        agent_id: 'agent-1',
        title: null,
        is_pinned: false,
        unread_count: 0,
        last_read_at: null,
        last_unread_at: null,
        last_activity_source: 'user',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      })
      .mockResolvedValueOnce({
        id: 'conversation-second',
        agent_id: 'agent-1',
        title: null,
        is_pinned: false,
        unread_count: 0,
        last_read_at: null,
        last_unread_at: null,
        last_activity_source: 'user',
        created_at: '2026-01-01T00:00:01Z',
        updated_at: '2026-01-01T00:00:01Z',
      })
    const onConversationId = vi.fn()

    const { result, rerender } = renderHook(
      ({ isDraftConversation }) =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { initialProps: { isDraftConversation: true } },
    )

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-first')
    })

    rerender({ isDraftConversation: false })
    expect(result.current.conversationId).toBeNull()
    rerender({ isDraftConversation: true })

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-second')
    })
    expect(mocks.createDraft).toHaveBeenCalledTimes(2)
    expect(onConversationId).toHaveBeenNthCalledWith(1, 'conversation-first')
    expect(onConversationId).toHaveBeenNthCalledWith(2, 'conversation-second')
  })

  it('deletes an uncommitted draft conversation when leaving the draft route', async () => {
    const onConversationId = vi.fn()

    const { result, rerender } = renderHook(
      ({ isDraftConversation }) =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { initialProps: { isDraftConversation: true } },
    )

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-v3')
    })

    rerender({ isDraftConversation: false })

    await waitFor(() => {
      expect(mocks.delete).toHaveBeenCalledWith('conversation-v3')
    })
  })

  it('keeps a committed draft conversation when leaving the draft route', async () => {
    const onConversationId = vi.fn()

    const { result, rerender } = renderHook(
      ({ isDraftConversation }) =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { initialProps: { isDraftConversation: true } },
    )

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-v3')
    })

    expect(result.current.commitDraftConversation()).toBe('conversation-v3')
    rerender({ isDraftConversation: false })

    expect(mocks.delete).not.toHaveBeenCalled()
  })

  it('keeps a submitted draft conversation before route promotion is accepted', async () => {
    const onConversationId = vi.fn()

    const { result, rerender } = renderHook(
      ({ isDraftConversation }) =>
        useLangGraphDraftConversation({
          agentId: 'agent-1',
          isDraftConversation,
          runtimeMode: 'langgraph_v3',
          onConversationId,
        }),
      { initialProps: { isDraftConversation: true } },
    )

    await waitFor(() => {
      expect(result.current.conversationId).toBe('conversation-v3')
    })

    expect(result.current.retainDraftConversation()).toBe('conversation-v3')
    rerender({ isDraftConversation: false })

    expect(mocks.delete).not.toHaveBeenCalled()
  })
})
