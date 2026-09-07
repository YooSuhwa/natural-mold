import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MessageRunSummary } from '../run-summary'

const mocks = vi.hoisted(() => ({
  state: {
    message: { id: 'lc_run--final', status: { type: 'running' } },
    thread: { isRunning: true, messages: [{ id: 'lc_run--final' }] },
  },
  useMessageRunSummary: vi.fn(() => ({ summary: null, isLoading: false })),
}))

vi.mock('@assistant-ui/react', () => ({
  useAuiState: (selector: (state: typeof mocks.state) => unknown) => selector(mocks.state),
}))
vi.mock('@/components/chat/conversation-context', () => ({
  useChatConversationId: () => 'conversation-1',
}))
vi.mock('@/lib/hooks/use-message-run-summary', () => ({
  useMessageRunSummary: mocks.useMessageRunSummary,
}))

describe('MessageRunSummary', () => {
  beforeEach(() => {
    mocks.state.thread.isRunning = true
    mocks.useMessageRunSummary.mockClear()
  })

  it('passes the actual thread run lifecycle into the bounded resolver', () => {
    const result = render(<MessageRunSummary />)

    expect(mocks.useMessageRunSummary).toHaveBeenLastCalledWith(
      'conversation-1',
      'lc_run--final',
      ['lc_run--final'],
      true,
    )

    mocks.state.thread.isRunning = false
    result.rerender(<MessageRunSummary />)

    expect(mocks.useMessageRunSummary).toHaveBeenLastCalledWith(
      'conversation-1',
      'lc_run--final',
      ['lc_run--final'],
      false,
    )
  })
})
