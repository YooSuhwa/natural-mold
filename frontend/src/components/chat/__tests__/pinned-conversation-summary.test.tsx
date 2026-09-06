import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'

const mocks = vi.hoisted(() => ({
  messageId: 'message-1',
  isRunning: false,
  summary: null as null | {
    source_message_id: string
    source_status: 'current' | 'changed' | 'deleted' | 'other_branch'
    snapshot_text: string
  },
  pin: vi.fn(async () => undefined),
  unpin: vi.fn(async () => undefined),
}))

vi.mock('@assistant-ui/react', () => ({
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({
      message: {
        id: mocks.messageId,
        status: { type: mocks.isRunning ? 'running' : 'complete' },
      },
    }),
}))

vi.mock('@/lib/hooks/use-pinned-conversation-summary', () => ({
  usePinnedConversationSummary: () => ({
    summary: mocks.summary,
    isLoading: false,
    isMutating: false,
    pin: mocks.pin,
    unpin: mocks.unpin,
  }),
}))

import { PinConversationSummaryButton } from '../pin-conversation-summary-button'
import { PinnedConversationSummary } from '../pinned-conversation-summary'

describe('PinnedConversationSummary', () => {
  beforeEach(() => {
    mocks.summary = null
    mocks.messageId = 'message-1'
    mocks.isRunning = false
    vi.clearAllMocks()
  })

  it.each([
    ['changed', '원본 메시지가 변경됨'],
    ['deleted', '원본 메시지가 삭제됨'],
    ['other_branch', '다른 대화 분기의 메시지'],
  ] as const)('renders the persisted snapshot and labels a %s source', (sourceStatus, label) => {
    // Given
    mocks.summary = {
      source_message_id: 'message-1',
      source_status: sourceStatus,
      snapshot_text: '사용자가 선택한 원본 요약',
    }

    // When
    render(<PinnedConversationSummary conversationId="conversation-1" />)

    // Then
    expect(screen.getByText('사용자가 선택한 원본 요약')).toBeVisible()
    expect(screen.getByText(label)).toBeVisible()
  })

  it('unpins from the persisted summary control', async () => {
    // Given
    mocks.summary = {
      source_message_id: 'message-1',
      source_status: 'current',
      snapshot_text: '요약',
    }
    const user = (await import('@testing-library/user-event')).default.setup()
    render(<PinnedConversationSummary conversationId="conversation-1" />)

    // When
    await user.click(screen.getByRole('button', { name: '대화 요약 고정 해제' }))

    // Then
    expect(mocks.unpin).toHaveBeenCalledOnce()
  })

  it('pins the current assistant message', async () => {
    // Given
    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <PinConversationSummaryButton
        conversationId="conversation-1"
        pinLabel="요약으로 고정"
        unpinLabel="요약 고정 해제"
      />,
    )

    // When
    await user.click(screen.getByRole('button', { name: '요약으로 고정' }))

    // Then
    expect(mocks.pin).toHaveBeenCalledWith('message-1')
  })

  it('preserves assistant-ui disambiguation for repeated source ids across turns', async () => {
    // Given
    mocks.messageId = 'message-1::moldy-turn-2'
    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <PinConversationSummaryButton
        conversationId="conversation-1"
        pinLabel="요약으로 고정"
        unpinLabel="요약 고정 해제"
      />,
    )

    // When
    await user.click(screen.getByRole('button', { name: '요약으로 고정' }))

    // Then
    expect(mocks.pin).toHaveBeenCalledWith('message-1::moldy-turn-2')
  })

  it('unpins when the current message is the selected summary source', async () => {
    // Given
    mocks.summary = {
      source_message_id: 'message-1',
      source_status: 'current',
      snapshot_text: '요약',
    }
    const user = (await import('@testing-library/user-event')).default.setup()
    render(
      <PinConversationSummaryButton
        conversationId="conversation-1"
        pinLabel="요약으로 고정"
        unpinLabel="요약 고정 해제"
      />,
    )

    // When
    await user.click(screen.getByRole('button', { name: '요약 고정 해제' }))

    // Then
    expect(mocks.unpin).toHaveBeenCalledOnce()
  })

  it('prevents pinning a message while it is still running', () => {
    // Given
    mocks.isRunning = true

    // When
    render(
      <PinConversationSummaryButton
        conversationId="conversation-1"
        pinLabel="요약으로 고정"
        unpinLabel="요약 고정 해제"
      />,
    )

    // Then
    expect(screen.getByRole('button', { name: '요약으로 고정' })).toBeDisabled()
  })
})
