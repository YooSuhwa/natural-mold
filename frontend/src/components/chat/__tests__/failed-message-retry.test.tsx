import { useCallback, useMemo, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import { useFailedInputRetryAction } from '@/lib/chat/commands/failed-input-retry'
import {
  FailedMessageRetryButton,
  FailedMessageRetryProvider,
  useFailedMessageRetryAction,
} from '@/components/chat/failed-message-retry'

function input(overrides: Partial<ConversationRunInput> = {}): ConversationRunInput {
  return {
    id: 'input-failed',
    conversation_id: 'conversation-1',
    run_id: 'run-failed',
    client_request_id: 'request-failed',
    source: 'chat',
    status: 'failed',
    priority: 0,
    position: 1,
    revision: 1,
    input_payload: { messages: [] },
    resource_context: [],
    attachment_ids: [],
    checkpoint_id: null,
    claimed_at: '2026-09-07T00:00:00Z',
    created_at: '2026-09-07T00:00:00Z',
    updated_at: '2026-09-07T00:00:00Z',
    ...overrides,
  }
}

function RetryHarness({
  failedRunId = 'run-failed',
  inputs = [input()],
  messageId = 'moldy-failed-run-failed',
  outcome = 'accept',
}: {
  readonly failedRunId?: string
  readonly inputs?: readonly ConversationRunInput[]
  readonly messageId?: string
  readonly outcome?: 'accept' | 'reject'
}) {
  const [result, setResult] = useState('idle')
  const [attemptIds, setAttemptIds] = useState<readonly string[]>([])
  const [release, setRelease] = useState<(() => void) | null>(null)
  const retry = useCallback(
    async (candidate: ConversationRunInput) => {
      setAttemptIds((current) => [...current, candidate.id])
      if (outcome === 'reject') {
        setResult(`rejected:${candidate.id}`)
        throw new Error('retry rejected')
      }
      await new Promise<void>((resolve) => setRelease(() => resolve))
      setResult(`accepted:${candidate.id}`)
    },
    [outcome],
  )
  const retryAction = useFailedInputRetryAction(inputs, failedRunId, retry)
  const context = useMemo(() => ({ failedRunId, retryAction }), [failedRunId, retryAction])

  return (
    <FailedMessageRetryProvider value={context}>
      <FailedMessageRetryButton label="Retry failed input" messageId={messageId}>
        Retry failed input
      </FailedMessageRetryButton>
      <button type="button" onClick={() => release?.()}>
        Accept retry
      </button>
      <output data-testid="retry-attempt-ids">{attemptIds.join(',')}</output>
      <output>{result}</output>
    </FailedMessageRetryProvider>
  )
}

function LegacyResolution() {
  const resolution = useFailedMessageRetryAction('moldy-failed-run-failed')
  return <output>{resolution.kind}</output>
}

describe('FailedMessageRetryButton', () => {
  it('retains the legacy action path when no recovery provider is wired', () => {
    render(<LegacyResolution />)

    expect(screen.getByText('legacy')).toBeInTheDocument()
  })

  it('retries only the exact failed run once after the retry is accepted', async () => {
    render(
      <RetryHarness
        inputs={[
          input({ id: 'input-prior', run_id: 'run-complete', status: 'claimed' }),
          input({ id: 'input-pending', run_id: null, status: 'pending' }),
          input(),
        ]}
      />,
    )

    const retry = screen.getByRole('button', { name: 'Retry failed input' })
    fireEvent.click(retry)
    fireEvent.click(retry)
    expect(screen.getByTestId('retry-attempt-ids')).toHaveTextContent(/^input-failed$/)
    expect(screen.queryByRole('button', { name: 'Retry failed input' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Accept retry' }))
    expect(await screen.findByText('accepted:input-failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry failed input' })).not.toBeInTheDocument()
  })

  it('stays unavailable for an accepted pending input or an unmatched historical failure', () => {
    const { rerender } = render(<RetryHarness inputs={[input({ status: 'pending' })]} />)
    expect(screen.queryByRole('button', { name: 'Retry failed input' })).not.toBeInTheDocument()

    rerender(<RetryHarness failedRunId="run-failed" messageId="moldy-failed-run-history" />)
    expect(screen.queryByRole('button', { name: 'Retry failed input' })).not.toBeInTheDocument()
  })

  it('does not restore retry after the shared action rejects', async () => {
    render(<RetryHarness outcome="reject" />)

    fireEvent.click(screen.getByRole('button', { name: 'Retry failed input' }))
    expect(await screen.findByText('rejected:input-failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry failed input' })).not.toBeInTheDocument()
  })
})
