import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useHitlDecisionController } from '../use-hitl-decision-controller'
import type { ResolvedInterruptToolCall } from '../hitl-interrupts'
import type { StandardInterruptPayload } from '@/lib/types'

vi.mock('../runtime-warning', () => ({ reportRuntimeFailure: vi.fn() }))

function approvalPayload(interruptId: string): StandardInterruptPayload {
  return {
    interrupt_id: interruptId,
    action_requests: [{ name: 'execute_in_skill', args: { command: 'node create_docx.cjs' } }],
    review_configs: [{ action_name: 'execute_in_skill', allowed_decisions: ['approve', 'reject'] }],
  }
}

type UpdateResolved = Parameters<typeof useHitlDecisionController>[0]['updateResolvedInterrupts']

function resolvedResults(updateResolved: ReturnType<typeof vi.fn<UpdateResolved>>) {
  return updateResolved.mock.calls.reduce<readonly ResolvedInterruptToolCall[]>(
    (current, [update]) => update(current),
    [],
  )
}

describe.each([false, true])('accepted HITL completion (concurrent: %s)', (concurrent) => {
  const payloads = concurrent
    ? [approvalPayload('interrupt-a'), approvalPayload('interrupt-b')]
    : [approvalPayload('interrupt-a')]

  it('preserves rejected results when resumed interrupts disappear before acceptance settles', async () => {
    const acceptance = Promise.withResolvers<void>()
    const stream = {
      respond: vi.fn(() => acceptance.promise),
      respondAll: vi.fn(() => acceptance.promise),
    }
    const updateResolvedInterrupts = vi.fn<UpdateResolved>()
    const { result, rerender } = renderHook(
      ({ activePayloads }: { readonly activePayloads: readonly StandardInterruptPayload[] }) => {
        const payloadsById = new Map(
          activePayloads.map((payload) => [payload.interrupt_id, payload]),
        )
        return useHitlDecisionController({
          conversationId: 'conversation-reject',
          stream,
          interruptPayloads: activePayloads,
          interruptPayloadsById: payloadsById,
          allInterruptPayloadsById: payloadsById,
          refreshLifecycle: vi.fn(async () => undefined),
          updateResolvedInterrupts,
        })
      },
      { initialProps: { activePayloads: payloads } },
    )

    const decisions = payloads.map((payload) =>
      result.current.onResumeDecisions([{ type: 'reject' }], 'rejected', payload.interrupt_id),
    )
    expect(concurrent ? stream.respondAll : stream.respond).toHaveBeenCalledOnce()
    rerender({ activePayloads: [] })
    await act(async () => {
      acceptance.resolve()
      await Promise.all(decisions)
    })

    expect(resolvedResults(updateResolvedInterrupts).map((item) => item.result)).toEqual(
      payloads.map(() => ({ decision: 'rejected' })),
    )
  })

  it('records accepted rejection before lifecycle refresh and preserves it if refresh fails', async () => {
    const refresh = Promise.withResolvers<void>()
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const updateResolvedInterrupts = vi.fn<UpdateResolved>()
    const refreshLifecycle = vi.fn(() => refresh.promise)
    const payloadsById = new Map(payloads.map((payload) => [payload.interrupt_id, payload]))
    const { result } = renderHook(() =>
      useHitlDecisionController({
        conversationId: 'conversation-reject-refresh',
        stream,
        interruptPayloads: payloads,
        interruptPayloadsById: payloadsById,
        allInterruptPayloadsById: payloadsById,
        refreshLifecycle,
        updateResolvedInterrupts,
      }),
    )

    const decisions = payloads.map((payload) =>
      result.current.onResumeDecisions([{ type: 'reject' }], 'rejected', payload.interrupt_id),
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(refreshLifecycle).toHaveBeenCalledOnce()
    const resultsBeforeRefresh = resolvedResults(updateResolvedInterrupts)
    refresh.reject(new Error('lifecycle unavailable'))
    await act(async () => {
      await Promise.all(decisions)
    })

    expect(resultsBeforeRefresh.map((item) => item.result)).toEqual(
      payloads.map(() => ({ decision: 'rejected' })),
    )
    expect(resolvedResults(updateResolvedInterrupts)).toEqual(resultsBeforeRefresh)
  })
})
