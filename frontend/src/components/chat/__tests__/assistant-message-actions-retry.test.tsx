import { useCallback, useMemo, useState } from 'react'
import {
  AssistantRuntimeProvider,
  MessageProvider,
  fromThreadMessageLike,
  useLocalRuntime,
} from '@assistant-ui/react'
import { fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import { failedInputRetryAction } from '@/lib/chat/commands/failed-input-retry'
import { RetryButton } from '@/components/chat/assistant-message-actions'
import {
  FailedMessageRetryProvider,
  type FailedMessageRetryContextValue,
} from '@/components/chat/failed-message-retry'
import { render, screen } from '../../../../tests/test-utils'

type RecoverySurface = 'legacy' | 'available' | 'unavailable' | 'normal-message'

function failedInput(): ConversationRunInput {
  return {
    id: 'input-current-failed',
    conversation_id: 'conversation-1',
    run_id: 'run-current-failed',
    client_request_id: 'request-current-failed',
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
  }
}

function RetryButtonHarness({ surface }: { readonly surface: RecoverySurface }) {
  const [attemptedInputId, setAttemptedInputId] = useState<string | null>(null)
  const retry = useCallback(async (input: ConversationRunInput) => {
    setAttemptedInputId(input.id)
  }, [])
  const retryAction = useMemo(
    () =>
      surface === 'available' || surface === 'normal-message'
        ? failedInputRetryAction([failedInput()], 'run-current-failed', retry)
        : undefined,
    [retry, surface],
  )
  const recovery = useMemo<FailedMessageRetryContextValue | null>(
    () =>
      surface === 'legacy'
        ? null
        : {
            failedRunId: surface === 'available' ? 'run-current-failed' : undefined,
            retryAction,
          },
    [retryAction, surface],
  )
  const runtime = useLocalRuntime(
    { run: async () => ({ content: [] }) },
    {
      initialMessages: [
        {
          id: 'moldy-failed-run-current-failed',
          role: 'assistant',
          content: 'terminal failure',
        },
      ],
    },
  )
  const content = <RetryButton />
  const message = fromThreadMessageLike(
    {
      id:
        surface === 'normal-message'
          ? 'assistant-message-normal'
          : 'moldy-failed-run-current-failed',
      role: 'assistant',
      content: 'terminal failure',
    },
    surface === 'normal-message' ? 'assistant-message-normal' : 'moldy-failed-run-current-failed',
    { type: 'complete', reason: 'stop' },
  )

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <MessageProvider message={message} index={0}>
        {recovery ? (
          <FailedMessageRetryProvider value={recovery}>{content}</FailedMessageRetryProvider>
        ) : (
          content
        )}
        <output>{attemptedInputId ?? 'not-attempted'}</output>
      </MessageProvider>
    </AssistantRuntimeProvider>
  )
}

describe('RetryButton recovery adapter', () => {
  it('uses public AUI message context to distinguish legacy, failed, and unavailable retry surfaces', async () => {
    const { rerender } = render(<RetryButtonHarness surface="legacy" />)

    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument()

    rerender(<RetryButtonHarness surface="available" />)
    fireEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(await screen.findByText('input-current-failed')).toBeInTheDocument()

    rerender(<RetryButtonHarness surface="unavailable" />)
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()

    rerender(<RetryButtonHarness surface="normal-message" />)
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })
})
