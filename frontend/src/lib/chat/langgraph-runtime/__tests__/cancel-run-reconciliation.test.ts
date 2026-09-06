import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ConversationRun, ConversationRunStatus } from '@/lib/types'
import { followExactRunToTerminal } from '../cancel-run-reconciliation'

function run(status: ConversationRunStatus, id = 'run-1'): ConversationRun {
  return {
    id,
    conversation_id: 'conversation',
    agent_id: 'agent-1',
    parent_run_id: null,
    status,
    source: 'chat',
    worker_instance_id: null,
    interrupt_id: null,
    last_event_id: null,
    input_preview: null,
    error_code: null,
    error_message: null,
    cancel_requested_at: null,
    started_at: null,
    heartbeat_at: null,
    completed_at: null,
    created_at: '2026-09-07T00:00:00Z',
    updated_at: '2026-09-07T00:00:00Z',
    metrics: null,
  }
}

afterEach(() => vi.useRealTimers())

describe('followExactRunToTerminal', () => {
  it('follows repeated canceling states to the exact canceled terminal', async () => {
    vi.useFakeTimers()
    const readRun = vi
      .fn<(runId: string, signal: AbortSignal) => Promise<ConversationRun>>()
      .mockResolvedValueOnce(run('canceling'))
      .mockResolvedValueOnce(run('canceled'))
    const onTerminal = vi.fn()
    const follow = followExactRunToTerminal({
      runId: 'run-1',
      signal: new AbortController().signal,
      readRun,
      onTerminal,
      onRecoverableError: vi.fn(),
      retryMs: 10,
      requestTimeoutMs: 100,
    })

    await vi.advanceTimersByTimeAsync(10)
    await follow

    expect(readRun).toHaveBeenCalledTimes(2)
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(run('canceled'))
  })

  it('continues beyond the former ten-second window until an exact terminal arrives', async () => {
    vi.useFakeTimers()
    let reads = 0
    const onTerminal = vi.fn()
    const follow = followExactRunToTerminal({
      runId: 'run-1',
      signal: new AbortController().signal,
      readRun: vi.fn(async () => run(reads++ < 11 ? 'canceling' : 'completed')),
      onTerminal,
      onRecoverableError: vi.fn(),
      retryMs: 1_000,
      requestTimeoutMs: 100,
    })

    await vi.advanceTimersByTimeAsync(12_000)
    await follow

    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(run('completed'))
  })

  it('aborts a hung exact read and retries after a recoverable timeout', async () => {
    vi.useFakeTimers()
    let firstSignal: AbortSignal | undefined
    const readRun = vi
      .fn<(runId: string, signal: AbortSignal) => Promise<ConversationRun>>()
      .mockImplementationOnce(
        (_runId, signal) =>
          new Promise((_resolve, reject) => {
            firstSignal = signal
            signal.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            )
          }),
      )
      .mockResolvedValueOnce(run('canceled'))
    const onTerminal = vi.fn()
    const follow = followExactRunToTerminal({
      runId: 'run-1',
      signal: new AbortController().signal,
      readRun,
      onTerminal,
      onRecoverableError: vi.fn(),
      retryMs: 10,
      requestTimeoutMs: 100,
    })

    await vi.advanceTimersByTimeAsync(110)
    await follow

    expect(firstSignal?.aborted).toBe(true)
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(run('canceled'))
  })

  it('ignores another run ID and keeps following the accepted run', async () => {
    vi.useFakeTimers()
    const onTerminal = vi.fn()
    const follow = followExactRunToTerminal({
      runId: 'run-1',
      signal: new AbortController().signal,
      readRun: vi
        .fn<(runId: string, signal: AbortSignal) => Promise<ConversationRun>>()
        .mockResolvedValueOnce(run('canceled', 'other-run'))
        .mockResolvedValueOnce(run('completed')),
      onTerminal,
      onRecoverableError: vi.fn(),
      retryMs: 10,
      requestTimeoutMs: 100,
    })

    await vi.advanceTimersByTimeAsync(10)
    await follow

    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(run('completed'))
  })

  it('stops without a terminal callback when its lifecycle is aborted', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const onTerminal = vi.fn()
    const follow = followExactRunToTerminal({
      runId: 'run-1',
      signal: controller.signal,
      readRun: vi.fn(async () => run('canceling')),
      onTerminal,
      onRecoverableError: vi.fn(),
      retryMs: 10,
      requestTimeoutMs: 100,
    })

    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await follow

    expect(onTerminal).not.toHaveBeenCalled()
  })
})
