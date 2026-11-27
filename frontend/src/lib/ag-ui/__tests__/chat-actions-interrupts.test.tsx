import type { AgUiInterrupt, AgUiResumeEntry } from '@assistant-ui/react-ag-ui'
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { moldyAgUiInterruptPayloads, useAgUiHitlActions } from '../chat-actions-interrupts'

afterEach(cleanup)

function ask(id: string): AgUiInterrupt {
  return {
    id,
    reason: 'moldy.ask_user',
    metadata: { moldy: { type: 'ask_user', question: 'Continue?' } },
  }
}

function auth(id: string, attempt: number, subagentRunId?: string): AgUiInterrupt {
  return {
    id,
    reason: 'moldy.auth_required',
    subagentRunId,
    metadata: {
      moldy: { type: 'auth_required', auth_request_id: 'same-request', items: [], attempt },
    },
  }
}

function approvals(id: string): AgUiInterrupt {
  return {
    id,
    reason: 'moldy.tool_approval',
    metadata: {
      moldy: {
        action_requests: [
          { name: 'read', args: {} },
          { name: 'write', args: {} },
        ],
        review_configs: [
          {
            action_name: 'read',
            allowed_decisions: ['approve', 'reject'],
            session_consent_eligible: true,
          },
          { action_name: 'write', allowed_decisions: ['edit', 'reject'] },
        ],
      },
    },
  }
}

function setup(initial: readonly AgUiInterrupt[]) {
  let slots = initial
  const listeners = new Set<() => void>()
  const submit = vi.fn<(responses: readonly AgUiResumeEntry[]) => Promise<void>>(async () => {})
  const runtime = {
    unstable_getPendingInterrupts: () => slots,
    unstable_submitInterruptResponses: submit,
    thread: {
      subscribe: (notify: () => void) => {
        listeners.add(notify)
        return () => {
          listeners.delete(notify)
        }
      },
    },
  }
  return {
    ...renderHook(
      ({ conversationId }) => useAgUiHitlActions({ runtime, conversationId, agentId: 'agent' }),
      { initialProps: { conversationId: 'conversation' } },
    ),
    submit,
    replace(next: readonly AgUiInterrupt[], notify = true) {
      slots = next
      if (notify)
        act(() => {
          listeners.forEach((listener) => listener())
        })
    },
  }
}

describe('Moldy choices on the native AG-UI pending interrupts', () => {
  it('projects identical rich question arguments from a live slot and an explicitly granted historical slot', () => {
    const args = {
      mode: 'option_list',
      title: '출력 형식',
      question: '어떤 형식으로 받을까요?',
      options: ['HTML', 'Markdown'],
    }
    const base = { id: 'question', reason: 'moldy.ask_user', subagentRunId: 'child' }
    const live = { ...base, metadata: { moldy: { type: 'ask_user', ...args } } }
    const restored = {
      ...base,
      metadata: {
        moldy: { ...live.metadata.moldy, legacyInterruptAccess: true, actionable: true },
      },
    }
    const [result] = moldyAgUiInterruptPayloads([restored])
    expect([result]).toEqual(moldyAgUiInterruptPayloads([live]))
    expect(result.action_requests).toEqual([{ name: 'ask_user', args }])
    expect(restored.metadata.moldy).toMatchObject({ legacyInterruptAccess: true, actionable: true })
  })

  it('preserves similarly named fields inside reviewed tool arguments', () => {
    const args = { actionable: false, legacyInterruptAccess: 'user-supplied' }
    const slot = {
      id: 'review',
      reason: 'moldy.tool_approval',
      metadata: {
        moldy: {
          action_requests: [{ name: 'annotate', args }],
          review_configs: [{ action_name: 'annotate', allowed_decisions: ['approve'] }],
          legacyInterruptAccess: true,
        },
      },
    }
    expect(moldyAgUiInterruptPayloads([slot])[0].action_requests[0].args).toEqual(args)
  })

  it('groups auth re-asks per owner even when attempts arrive out of order', () => {
    const slots = [
      auth('current-id', 2),
      auth('stale-id', 1),
      auth('child-1', 1, 'child'),
      ask('same-request'),
    ]
    expect(moldyAgUiInterruptPayloads(slots).map((payload) => payload.interrupt_id)).toEqual([
      'current-id',
      'child-1',
      'same-request',
    ])
  })

  it('keeps unknown and passive slots non-actionable even if they have editable metadata', async () => {
    const passive = { ...ask('wait'), reason: 'moldy.workflow_wait' }
    const mismatched = { ...ask('wrong-shape'), reason: 'moldy.tool_approval' }
    expect(moldyAgUiInterruptPayloads([passive, mismatched])).toEqual([])
    const view = setup([ask('question'), passive])
    await expect(
      view.result.current.registerDecision(
        0,
        { type: 'respond', message: 'yes' },
        undefined,
        'question',
      ),
    ).rejects.toMatchObject({ name: 'InvalidStateError' })
    expect(view.submit).not.toHaveBeenCalled()
  })

  it('blocks every slot after a sibling becomes read-only, even before the UI is notified', async () => {
    const answerable = ask('historical-question')
    const readOnly: AgUiInterrupt = {
      ...answerable,
      metadata: { moldy: { type: 'ask_user', question: 'Previous question?', actionable: false } },
    }
    const active = approvals('current-tools')
    expect(moldyAgUiInterruptPayloads([active, readOnly])).toEqual([])

    const view = setup([active, answerable])
    const register = view.result.current.registerDecision
    view.replace([active, readOnly], false)
    await expect(register(0, { type: 'approve' }, undefined, active.id)).rejects.toMatchObject({
      name: 'InvalidStateError',
    })
    expect(view.submit).not.toHaveBeenCalled()
  })

  it('batches edits, consent, and a parallel question in native interrupt and reviewed action order', async () => {
    const view = setup([approvals('review'), ask('question')])
    const register = view.result.current.registerDecision
    const answer = { type: 'respond', message: 'go' } as const
    const edit = {
      type: 'edit',
      edited_action: { name: 'write', args: { text: 'revised' } },
    } as const
    const approve = { type: 'approve', scope: 'session' } as const
    await expect(register(0, answer)).rejects.toMatchObject({ name: 'InvalidStateError' })
    const partial = [
      register(0, answer, undefined, 'question'),
      register(1, edit, undefined, 'review'),
    ]
    await Promise.resolve()
    expect(view.submit).not.toHaveBeenCalled()
    await act(async () => {
      await Promise.all([...partial, register(0, approve, undefined, 'review')])
    })
    expect(view.submit.mock.calls).toEqual([
      [
        [
          { interruptId: 'review', status: 'resolved', payload: { decisions: [approve, edit] } },
          { interruptId: 'question', status: 'resolved', payload: { decisions: [answer] } },
        ],
      ],
    ])
  })

  it('submits once while the native SDK drains the previous stream', async () => {
    const view = setup([ask('question')])
    const result = await Promise.allSettled([
      view.result.current.onResumeDecisions([{ type: 'respond', message: 'first' }]),
      view.result.current.onResumeDecisions([{ type: 'respond', message: 'second' }]),
    ])
    expect(result.map((entry) => entry.status)).toEqual(['fulfilled', 'rejected'])
    expect(view.submit).toHaveBeenCalledTimes(1)
  })

  it('only allows the authoritative raw ID and attempt after a credential re-ask', async () => {
    const view = setup([auth('old', 1)])
    const stale = view.result.current.registerDecision
    view.replace([auth('new', 2)])
    const register = view.result.current.registerDecision
    const response = { type: 'respond', message: 'retry', attempt: 2 } as const
    for (const decision of [
      stale(0, response, undefined, 'old'),
      register(0, response, undefined, 'same-request'),
      register(0, { ...response, attempt: 1 }, undefined, 'new'),
    ])
      await expect(decision).rejects.toMatchObject({ name: 'InvalidStateError' })
    expect(view.submit).not.toHaveBeenCalled()
    await register(0, response, undefined, 'new')
    expect(view.submit).toHaveBeenCalledWith([
      { interruptId: 'new', status: 'resolved', payload: { decisions: [response] } },
    ])
  })

  it.each(['2020-01-01T00:00:00.000Z', 'not-a-timestamp'])(
    'rejects expiry %s before accepting even a partial review',
    async (expiresAt) => {
      const view = setup([{ ...approvals('review'), expiresAt }, ask('question')])
      await expect(
        view.result.current.registerDecision(0, { type: 'approve' }, undefined, 'review'),
      ).rejects.toMatchObject({ name: 'InvalidStateError' })
      expect(view.submit).not.toHaveBeenCalled()
    },
  )

  it('declines changed choices before the pending subscription publishes them', async () => {
    const view = setup([ask('question')])
    const answer = view.result.current.onResumeDecisions
    view.replace(
      [{ ...ask('question'), metadata: { moldy: { type: 'ask_user', question: 'Changed?' } } }],
      false,
    )
    await expect(answer([{ type: 'respond', message: 'yes' }])).rejects.toMatchObject({
      name: 'InvalidStateError',
    })
    expect(view.submit).not.toHaveBeenCalled()
  })

  it('invalidates saved handlers when the selected conversation changes or the UI unmounts', async () => {
    const view = setup([ask('question')])
    const first = view.result.current.onResumeDecisions
    view.rerender({ conversationId: 'next' })
    await expect(first([{ type: 'respond', message: 'yes' }])).rejects.toMatchObject({
      name: 'InvalidStateError',
    })
    const next = view.result.current.onResumeDecisions
    view.unmount()
    await expect(next([{ type: 'respond', message: 'yes' }])).rejects.toMatchObject({
      name: 'InvalidStateError',
    })
    expect(view.submit).not.toHaveBeenCalled()
  })
})
