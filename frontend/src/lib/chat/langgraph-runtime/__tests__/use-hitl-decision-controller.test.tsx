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

    await expect(
      result.current.onResumeDecisions([{ type: 'approve' }], 'approve', 'forged'),
    ).rejects.toMatchObject({ name: 'InvalidStateError' })

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

    const oldFirst = result.current.onResumeDecisions([{ type: 'approve' }], 'old a', 'interrupt-a')
    const oldFlush = result.current.onResumeDecisions([{ type: 'approve' }], 'old b', 'interrupt-b')
    await vi.waitFor(() => expect(stream.respondAll).toHaveBeenCalledTimes(1))
    rerender({ payloads: newPayloads })
    const newFirst = result.current.onResumeDecisions([{ type: 'approve' }], 'new a', 'interrupt-a')
    const newFinal = result.current.onResumeDecisions([{ type: 'approve' }], 'new b', 'interrupt-b')
    await Promise.all([newFirst, newFinal])

    expect(stream.respondAll).toHaveBeenCalledTimes(2)
    expect(stream.respondAll.mock.calls[1]?.[0]).toEqual({
      'interrupt-a': { decisions: [{ type: 'approve' }] },
      'interrupt-b': { decisions: [{ type: 'approve' }] },
    })
    await act(async () => {
      resolveOldFlush?.()
      await Promise.all([oldFirst, oldFlush])
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

  it('rejects an incomplete multi-action batch when its semantic generation is replaced', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const updateResolvedInterrupts = vi.fn()
    const oldPayload: StandardInterruptPayload = {
      ...payload,
      action_requests: [
        { name: 'ask_user', args: { question: '계속할까요?' } },
        { name: 'send_email', args: { to: 'old@example.com' } },
      ],
      review_configs: [
        { action_name: 'ask_user', allowed_decisions: ['respond'] },
        { action_name: 'send_email', allowed_decisions: ['approve', 'reject'] },
      ],
    }
    const nextPayload: StandardInterruptPayload = {
      ...oldPayload,
      namespace: ['tools:new-generation'],
      action_requests: [
        { name: 'ask_user', args: { question: '새 작업을 계속할까요?' } },
        { name: 'send_email', args: { to: 'new@example.com' } },
      ],
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
      { initialProps: { activePayload: oldPayload } },
    )

    const staleDecision = result.current.registerDecision(
      0,
      { type: 'respond', message: '네' },
      '네',
      oldPayload.interrupt_id,
    )
    const staleRejection = expect(staleDecision).rejects.toMatchObject({ name: 'AbortError' })

    rerender({ activePayload: nextPayload })
    await staleRejection

    const first = result.current.registerDecision(
      0,
      { type: 'respond', message: '계속' },
      '계속',
      nextPayload.interrupt_id,
    )
    const second = result.current.registerDecision(
      1,
      { type: 'approve' },
      '승인',
      nextPayload.interrupt_id,
    )
    await Promise.all([first, second])

    expect(stream.respond).toHaveBeenCalledOnce()
    expect(stream.respond).toHaveBeenCalledWith(
      {
        decisions: [{ type: 'respond', message: '계속' }, { type: 'approve' }],
      },
      { interruptId: nextPayload.interrupt_id, namespace: ['tools:new-generation'] },
    )
  })

  it('rejects every concurrent waiter on respondAll failure and retries the full set', async () => {
    const failure = new Error('respondAll rejected')
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce(undefined),
    }
    const payloads: StandardInterruptPayload[] = [
      { ...payload, interrupt_id: 'interrupt-a', namespace: ['tools:a'] },
      { ...payload, interrupt_id: 'interrupt-b', namespace: ['tools:b'] },
    ]
    const payloadsById = new Map(payloads.map((item) => [item.interrupt_id, item]))
    const { result } = renderHook(() =>
      useHitlDecisionController({
        conversationId: 'conversation-a',
        stream,
        interruptPayloads: payloads,
        interruptPayloadsById: payloadsById,
        allInterruptPayloadsById: payloadsById,
        refreshLifecycle: vi.fn(async () => undefined),
        updateResolvedInterrupts: vi.fn(),
      }),
    )

    const firstAttemptA = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve a',
      'interrupt-a',
    )
    const firstAttemptB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'reject b' }],
      'reject b',
      'interrupt-b',
    )
    await expect(Promise.all([firstAttemptA, firstAttemptB])).rejects.toBe(failure)

    const retryA = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve a',
      'interrupt-a',
    )
    const retryB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'reject b' }],
      'reject b',
      'interrupt-b',
    )
    await Promise.all([retryA, retryB])

    expect(stream.respondAll).toHaveBeenCalledTimes(2)
    for (const [responses] of stream.respondAll.mock.calls) {
      expect(responses).toEqual({
        'interrupt-a': { decisions: [{ type: 'approve' }] },
        'interrupt-b': { decisions: [{ type: 'reject', message: 'reject b' }] },
      })
    }
  })

  it('does not resend an accepted single decision when lifecycle refresh fails', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const refreshLifecycle = vi.fn().mockRejectedValue(new Error('refresh unavailable'))
    const payloadsById = new Map([[payload.interrupt_id, payload]])
    const { result } = renderHook(() =>
      useHitlDecisionController({
        conversationId: 'conversation-a',
        stream,
        interruptPayloads: [payload],
        interruptPayloadsById: payloadsById,
        allInterruptPayloadsById: payloadsById,
        refreshLifecycle,
        updateResolvedInterrupts: vi.fn(),
      }),
    )

    await expect(
      result.current.onResumeDecisions([{ type: 'approve' }], 'approve', payload.interrupt_id),
    ).resolves.toBeUndefined()
    expect(stream.respond).toHaveBeenCalledOnce()
  })

  it('resolves an accepted concurrent batch even when lifecycle refresh fails', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const payloads: StandardInterruptPayload[] = [
      { ...payload, interrupt_id: 'interrupt-a' },
      { ...payload, interrupt_id: 'interrupt-b' },
    ]
    const payloadsById = new Map(payloads.map((item) => [item.interrupt_id, item]))
    const { result } = renderHook(() =>
      useHitlDecisionController({
        conversationId: 'conversation-a',
        stream,
        interruptPayloads: payloads,
        interruptPayloadsById: payloadsById,
        allInterruptPayloadsById: payloadsById,
        refreshLifecycle: vi.fn().mockRejectedValue(new Error('refresh unavailable')),
        updateResolvedInterrupts: vi.fn(),
      }),
    )

    const first = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve a',
      'interrupt-a',
    )
    const second = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve b',
      'interrupt-b',
    )

    await expect(Promise.all([first, second])).resolves.toEqual([undefined, undefined])
    expect(stream.respondAll).toHaveBeenCalledOnce()
  })

  it('discards every old decision when one concurrent payload is semantically replaced', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const oldPayloads: StandardInterruptPayload[] = [
      {
        ...payload,
        interrupt_id: 'interrupt-a',
        namespace: ['tools:old-a'],
        action_requests: [{ name: 'send_email', args: { to: 'old-a@example.com' } }],
      },
      { ...payload, interrupt_id: 'interrupt-b', namespace: ['tools:b'] },
    ]
    const newPayloads: StandardInterruptPayload[] = [
      {
        ...oldPayloads[0],
        namespace: ['tools:new-a'],
        action_requests: [{ name: 'send_email', args: { to: 'new-a@example.com' } }],
      },
      oldPayloads[1],
    ]
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
          updateResolvedInterrupts: vi.fn(),
        })
      },
      { initialProps: { payloads: oldPayloads } },
    )

    const staleB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'old b' }],
      'old b',
      'interrupt-b',
    )
    const staleRejection = expect(staleB).rejects.toMatchObject({ name: 'AbortError' })

    rerender({ payloads: newPayloads })
    await staleRejection

    const newA = result.current.onResumeDecisions([{ type: 'approve' }], 'new a', 'interrupt-a')
    await Promise.resolve()
    expect(stream.respondAll).not.toHaveBeenCalled()

    const newB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'new b' }],
      'new b',
      'interrupt-b',
    )
    await Promise.all([newA, newB])

    expect(stream.respondAll).toHaveBeenCalledOnce()
    expect(stream.respondAll).toHaveBeenCalledWith({
      'interrupt-a': { decisions: [{ type: 'approve' }] },
      'interrupt-b': { decisions: [{ type: 'reject', message: 'new b' }] },
    })
  })

  it('migrates an incomplete batch when the active interrupt set expands', async () => {
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(async () => undefined),
    }
    const payloadA = { ...payload, interrupt_id: 'interrupt-a' }
    const payloadB = { ...payload, interrupt_id: 'interrupt-b' }
    const payloadC = { ...payload, interrupt_id: 'interrupt-c' }
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
          updateResolvedInterrupts: vi.fn(),
        })
      },
      { initialProps: { payloads: [payloadA, payloadB] } },
    )

    const originalWaiter = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve a',
      'interrupt-a',
    )
    await Promise.resolve()
    expect(stream.respondAll).not.toHaveBeenCalled()

    rerender({ payloads: [payloadA, payloadB, payloadC] })
    const expandedB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'reject b' }],
      'reject b',
      'interrupt-b',
    )
    const expandedC = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'approve c',
      'interrupt-c',
    )

    await expect(Promise.all([originalWaiter, expandedB, expandedC])).resolves.toEqual([
      undefined,
      undefined,
      undefined,
    ])
    expect(stream.respondAll).toHaveBeenCalledOnce()
    expect(stream.respondAll).toHaveBeenCalledWith({
      'interrupt-a': { decisions: [{ type: 'approve' }] },
      'interrupt-b': { decisions: [{ type: 'reject', message: 'reject b' }] },
      'interrupt-c': { decisions: [{ type: 'approve' }] },
    })
  })

  it('rejects a pure-removal migration instead of overwriting an in-flight target batch', async () => {
    let resolveOldBatch: (() => void) | undefined
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveOldBatch = resolve
          }),
      ),
    }
    const payloadA = { ...payload, interrupt_id: 'interrupt-a' }
    const payloadB = { ...payload, interrupt_id: 'interrupt-b' }
    const payloadC = { ...payload, interrupt_id: 'interrupt-c' }
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
          updateResolvedInterrupts: vi.fn(),
        })
      },
      { initialProps: { payloads: [payloadA, payloadB] } },
    )

    const oldA = result.current.onResumeDecisions([{ type: 'approve' }], 'old a', 'interrupt-a')
    const oldB = result.current.onResumeDecisions([{ type: 'approve' }], 'old b', 'interrupt-b')
    await vi.waitFor(() => expect(stream.respondAll).toHaveBeenCalledOnce())

    rerender({ payloads: [payloadA, payloadB, payloadC] })
    const expandedC = result.current.onResumeDecisions(
      [{ type: 'approve' }],
      'expanded c',
      'interrupt-c',
    )
    const expandedRejection = expect(expandedC).rejects.toMatchObject({ name: 'AbortError' })

    rerender({ payloads: [payloadA, payloadB] })
    await expandedRejection
    expect(stream.respondAll).toHaveBeenCalledOnce()

    resolveOldBatch?.()
    await Promise.all([oldA, oldB])
    expect(stream.respondAll).toHaveBeenCalledOnce()
  })

  it('prevents an old in-flight failure from deleting a newer overlapping decision', async () => {
    const oldFailure = new Error('old batch transport failed')
    let rejectOldBatch: ((reason: Error) => void) | undefined
    const stream = {
      respond: vi.fn(async () => undefined),
      respondAll: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<void>((_resolve, reject) => {
              rejectOldBatch = reject
            }),
        )
        .mockResolvedValueOnce(undefined),
    }
    const oldPayloads: StandardInterruptPayload[] = [
      {
        ...payload,
        interrupt_id: 'interrupt-a',
        namespace: ['tools:old-a'],
        action_requests: [{ name: 'send_email', args: { to: 'old-a@example.com' } }],
      },
      { ...payload, interrupt_id: 'interrupt-b', namespace: ['tools:b'] },
    ]
    const newPayloads: StandardInterruptPayload[] = [
      {
        ...oldPayloads[0],
        namespace: ['tools:new-a'],
        action_requests: [{ name: 'send_email', args: { to: 'new-a@example.com' } }],
      },
      oldPayloads[1],
    ]
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
          updateResolvedInterrupts: vi.fn(),
        })
      },
      { initialProps: { payloads: oldPayloads } },
    )

    const oldA = result.current.onResumeDecisions([{ type: 'approve' }], 'old a', 'interrupt-a')
    const oldB = result.current.onResumeDecisions([{ type: 'approve' }], 'old b', 'interrupt-b')
    const oldRejection = expect(Promise.all([oldA, oldB])).rejects.toBe(oldFailure)
    await vi.waitFor(() => expect(stream.respondAll).toHaveBeenCalledOnce())

    rerender({ payloads: newPayloads })
    const newB = result.current.onResumeDecisions(
      [{ type: 'reject', message: 'new b' }],
      'new b',
      'interrupt-b',
    )

    rejectOldBatch?.(oldFailure)
    await oldRejection

    const newA = result.current.onResumeDecisions([{ type: 'approve' }], 'new a', 'interrupt-a')
    await Promise.all([newA, newB])

    expect(stream.respondAll).toHaveBeenCalledTimes(2)
    expect(stream.respondAll.mock.calls[1]?.[0]).toEqual({
      'interrupt-a': { decisions: [{ type: 'approve' }] },
      'interrupt-b': { decisions: [{ type: 'reject', message: 'new b' }] },
    })
  })
})
