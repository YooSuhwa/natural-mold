import { describe, expect, it, vi } from 'vitest'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import { failedInputRetryAction } from '../failed-input-retry'

function runInput(overrides: Partial<ConversationRunInput> = {}): ConversationRunInput {
  return {
    id: 'input-1',
    conversation_id: 'conversation-1',
    run_id: 'run-failed',
    client_request_id: 'request-original',
    source: 'user',
    status: 'claimed',
    priority: 0,
    position: 1,
    revision: 1,
    input_payload: { messages: [] },
    resource_context: [],
    attachment_ids: [],
    checkpoint_id: null,
    claimed_at: '2026-09-06T00:00:00Z',
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

describe('failedInputRetryAction', () => {
  it('selects only the accepted input bound to the exact failed run', async () => {
    const retry = vi.fn(async () => undefined)
    const prior = runInput({ id: 'input-prior', run_id: 'run-completed' })
    const pending = runInput({ id: 'input-pending', run_id: null, status: 'pending' })
    const failed = runInput({ id: 'input-failed' })

    const action = failedInputRetryAction([prior, pending, failed], 'run-failed', retry)

    expect(action?.failedInputId).toBe('input-failed')
    await action?.execute('input-failed')
    await action?.execute('input-failed')
    expect(retry).toHaveBeenCalledExactlyOnceWith(failed)
  })

  it('stays unavailable when historical input correlation is absent', () => {
    expect(
      failedInputRetryAction([runInput({ run_id: 'another-run' })], 'run-failed', vi.fn()),
    ).toBeUndefined()
  })
})
