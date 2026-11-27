import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessageProvider,
  fromThreadMessageLike,
  useLocalRuntime,
} from '@assistant-ui/react'
import { act, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RetryButton } from '@/components/chat/assistant-message-actions'
import {
  FailedMessageRetryProvider,
  type FailedMessageRetryContextValue,
} from '@/components/chat/failed-message-retry'
import { render, screen } from '../../../../tests/test-utils'

const safeText = '비교표 첫 행 <b>한글</b>'
const evidence = {
  text: safeText,
  tools: [{ name: 'send_email', state: 'unknown' as const }],
  truncated: false,
}
const messageId = 'moldy-failed-run-1'

function RecoveryChat({
  value,
  run,
}: {
  readonly value: FailedMessageRetryContextValue
  readonly run: () => Promise<{ content: [] }>
}) {
  const runtime = useLocalRuntime(
    { run },
    { initialMessages: [{ id: messageId, role: 'assistant', content: 'interrupted' }] },
  )
  const message = fromThreadMessageLike(
    { id: messageId, role: 'assistant', content: 'interrupted' },
    messageId,
    { type: 'complete', reason: 'stop' },
  )
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <MessageProvider message={message} index={0}>
        <FailedMessageRetryProvider value={value}>
          <RetryButton />
        </FailedMessageRetryProvider>
      </MessageProvider>
      <ComposerPrimitive.Input aria-label="thread draft" />
    </AssistantRuntimeProvider>
  )
}

describe('partial stream recovery', () => {
  it('rehydrates plain evidence and prepares an editable draft without sending or replaying tools', async () => {
    const run = vi.fn<() => Promise<{ content: [] }>>(async () => ({ content: [] }))
    const execute = vi.fn<() => Promise<void>>()
    const value = {
      failedRunId: 'run-1',
      partialOutput: evidence,
      retryAction: { failedInputId: 'original-input', execute },
    }
    const first = render(<RecoveryChat value={value} run={run} />)
    await act(async () => {})
    expect(screen.getByText(safeText)).toBeInTheDocument()
    expect(first.container.querySelector('b')).toBeNull()
    expect(screen.getByText(/send_email/)).toBeInTheDocument()
    first.unmount()

    render(<RecoveryChat value={value} run={run} />)
    await act(async () => {})
    const draft = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'thread draft' })
    const continueButton = screen.getByRole('button', { name: '이어서 작성' })
    expect(draft).toHaveValue('')
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
    fireEvent.change(draft, { target: { value: '사용자가 입력한 새 질문' } })
    expect(continueButton).toBeDisabled()
    await act(async () => {
      fireEvent.click(continueButton)
    })
    expect(draft).toHaveValue('사용자가 입력한 새 질문')

    fireEvent.change(draft, { target: { value: '' } })
    await act(async () => {
      fireEvent.click(continueButton)
    })
    expect(draft.value).toContain(safeText)
    expect(draft.value).toContain('send_email')
    expect(execute).not.toHaveBeenCalled()
    expect(run).not.toHaveBeenCalled()
  })

  it('withholds the projection and replay action for unproven branches and unrelated messages', async () => {
    const run = vi.fn<() => Promise<{ content: [] }>>(async () => ({ content: [] }))
    const execute = vi.fn<() => Promise<void>>()
    const retryAction = { failedInputId: 'original-input', execute }
    const { rerender } = render(
      <RecoveryChat value={{ failedRunId: 'run-1', partialOutput: null, retryAction }} run={run} />,
    )
    await act(async () => {})
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '이어서 작성' })).not.toBeInTheDocument()
    rerender(
      <RecoveryChat
        value={{ failedRunId: 'another-run', partialOutput: evidence, retryAction }}
        run={run}
      />,
    )
    await act(async () => {})
    expect(screen.queryByText(safeText)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '이어서 작성' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
    expect(execute).not.toHaveBeenCalled()
    expect(run).not.toHaveBeenCalled()
  })
})
