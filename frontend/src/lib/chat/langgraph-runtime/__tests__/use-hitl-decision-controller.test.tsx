import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useHitlDecisionController } from '../use-hitl-decision-controller'
import type { ResolvedInterruptToolCall } from '../hitl-interrupts'
import type { StandardInterruptPayload } from '@/lib/types'

const payload: StandardInterruptPayload = {
  interrupt_id: 'interrupt-1',
  action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
  review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
}

describe('useHitlDecisionController', () => {
  it('drops completion side effects after the conversation lifetime changes', async () => {
    let resolveRespond: (() => void) | undefined
    const stream = {
      respond: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveRespond = resolve
          }),
      ),
      respondAll: vi.fn(async () => undefined),
    }
    const refreshLifecycle = vi.fn(async () => undefined)
    const updateResolvedInterrupts = vi.fn()
    const payloads = [payload]
    const payloadsById = new Map([[payload.interrupt_id, payload]])
    const { result, rerender } = renderHook(
      ({ conversationId }: { conversationId: string }) =>
        useHitlDecisionController({
          conversationId,
          stream,
          interruptPayloads: payloads,
          interruptPayloadsById: payloadsById,
          allInterruptPayloadsById: payloadsById,
          refreshLifecycle,
          updateResolvedInterrupts,
        }),
      { initialProps: { conversationId: 'conversation-a' } },
    )

    const resume = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve',
      payload.interrupt_id,
    )
    rerender({ conversationId: 'conversation-b' })
    await act(async () => {
      resolveRespond?.()
      await resume
    })

    expect(stream.respond).toHaveBeenCalledOnce()
    expect(updateResolvedInterrupts).not.toHaveBeenCalled()
  })

  it('rejects an interrupt id that is not in the active payload map', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const { result } = renderHook(() =>
      useHitlDecisionController({
        conversationId: 'conversation-a',
        stream,
        interruptPayloads: [payload],
        interruptPayloadsById: new Map([[payload.interrupt_id, payload]]),
        allInterruptPayloadsById: new Map([[payload.interrupt_id, payload]]),
        refreshLifecycle: vi.fn(async () => undefined),
        updateResolvedInterrupts: vi.fn(),
      }),
    )

    await result.current.onResumeDecisions([{ type: 'approve' }], 'approve', 'forged')

    expect(stream.respond).not.toHaveBeenCalled()
    expect(stream.respondAll).not.toHaveBeenCalled()
  })

  it('drops an old same-id completion after the payload generation changes', async () => {
    let resolveRespond: (() => void) | undefined
    const stream = {
      respond: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveRespond = resolve
          }),
      ),
      respondAll: vi.fn(async () => undefined),
    }
    const updateResolvedInterrupts = vi.fn()
    const nextPayload: StandardInterruptPayload = {
      ...payload,
      namespace: ['tools:new-generation'],
      action_requests: [{ name: 'send_email', args: { to: 'new@example.com' } }],
    }
    const { result, rerender } = renderHook(
      ({ activePayload }: { activePayload: StandardInterruptPayload }) => {
        const payloadsById = new Map([[activePayload.interrupt_id, activePayload]])
        return useHitlDecisionController({
          conversationId: 'conversation-a',
          stream,
          interruptPayloads: [activePayload],
          interruptPayloadsById: payloadsById,
          allInterruptPayloadsById: payloadsById,
          refreshLifecycle: vi.fn(async () => undefined),
          updateResolvedInterrupts,
        })
      },
      { initialProps: { activePayload: payload } },
    )

    const resume = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve',
      payload.interrupt_id,
    )
    rerender({ activePayload: nextPayload })
    await act(async () => {
      resolveRespond?.()
      await resume
    })

    expect(stream.respond).toHaveBeenCalledOnce()
    expect(updateResolvedInterrupts).not.toHaveBeenCalled()
  })

  it('allows a new semantic generation to flush while the old respondAll is in flight', async () => {
    let resolveOldFlush: (() => void) | undefined
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<void>((resolve) => {
              resolveOldFlush = resolve
            }),
        )
        .mockResolvedValueOnce(undefined),
    }
    const updateResolvedInterrupts = vi.fn()
    const oldPayloads: StandardInterruptPayload[] = [
      { ...payload, interrupt_id: 'interrupt-a', namespace: ['old:a'] },
      { ...payload, interrupt_id: 'interrupt-b', namespace: ['old:b'] },
    ]
    const newPayloads: StandardInterruptPayload[] = oldPayloads.map((item) => ({
      ...item,
      namespace: [`new:${item.interrupt_id}`],
      action_requests: [
        { name: 'send_email', args: { to: `new-${item.interrupt_id}@example.com` } },
      ],
    }))
    const { result, rerender } = renderHook(
      ({ payloads }: { payloads: StandardInterruptPayload[] }) => {
        const payloadsById = new Map(payloads.map((item) => [item.interrupt_id, item]))
        return useHitlDecisionController({
          conversationId: 'conversation-a',
          stream,
          interruptPayloads: payloads,
          interruptPayloadsById: payloadsById,
          allInterruptPayloadsById: payloadsById,
          refreshLifecycle: vi.fn(async () => undefined),
          updateResolvedInterrupts,
        })
      },
      { initialProps: { payloads: oldPayloads } },
    )

    await result.current.onResumeDecisions([{ type: 'approve' }], 'old a', 'interrupt-a')
    const oldFlush = result.current.onResumeDecisions([{ type: 'approve' }], 'old b', 'interrupt-b')
    rerender({ payloads: newPayloads })
    await result.current.onResumeDecisions([{ type: 'approve' }], 'new a', 'interrupt-a')
    await result.current.onResumeDecisions([{ type: 'approve' }], 'new b', 'interrupt-b')

    expect(stream.respondAll).toHaveBeenCalledTimes(2)
    expect(stream.respondAll.mock.calls[1]?.[0]).toEqual({
      'interrupt-a': { decisions: [{ type: 'approve' }] },
      'interrupt-b': { decisions: [{ type: 'approve' }] },
    })
    await act(async () => {
      resolveOldFlush?.()
      await oldFlush
    })
    expect(stream.respondAll).toHaveBeenCalledTimes(2)
    let resolved: readonly ResolvedInterruptToolCall[] = []
    for (const [updater] of updateResolvedInterrupts.mock.calls) {
      resolved = updater(resolved)
    }
    expect(resolved).toHaveLength(2)
    expect(JSON.stringify(resolved)).toContain('new-interrupt-a@example.com')
    expect(JSON.stringify(resolved)).toContain('new-interrupt-b@example.com')
    expect(JSON.stringify(resolved)).not.toContain('team@example.com')
  })
})
