import { describe, expect, it } from 'vitest'

import {
  authRequiredDecision,
  authRequiredItems,
  toStandardInterruptPayload,
} from '@/lib/chat/auth-required-interrupt'
import {
  activeInterruptPayloads,
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadFromInterrupt,
  standardPayloadsFromInterrupts,
} from '@/lib/chat/langgraph-runtime/hitl-interrupts'
import { standardInterruptToToolCalls } from '@/lib/chat/standard-interrupt'

const value = {
  type: 'auth_required',
  reason: 'expired',
  items: [
    {
      resource_type: 'mcp_server',
      resource_name: 'Slack',
      tool_names: ['post_message', 'read_channel'],
      credential_id: 'c1',
      fixable_by: 'me',
      connect: { kind: 'oauth' },
    },
    { resource_name: 'Jira', tool_names: ['create_issue'], fixable_by: 'owner' },
  ],
  allowed_decisions: ['respond'],
}

describe('auth_required interrupts', () => {
  it('normalizes the bundled payload into one respond-only auth_required tool call', () => {
    const payload = standardPayloadFromInterrupt({ id: 'int-1', value, ns: ['agent:1'] })
    expect(payload).toMatchObject({
      interrupt_id: 'int-1',
      namespace: ['agent:1'],
      native: 'auth_required',
      review_configs: [{ action_name: 'moldy_auth_required', allowed_decisions: ['respond'] }],
    })

    if (!payload) throw new Error('auth_required payload was not recognized')
    const [call] = standardInterruptToToolCalls(payload)
    expect(call.name).toBe('moldy_auth_required')
    expect(call.id).toBe('int-1:0#1')
    expect(call.args).toMatchObject({ reason: 'expired', attempt: 1, hitl_action_index: 0 })
    // The OAuth connect needs the credential id to survive display redaction.
    expect(authRequiredItems(call.args)[0]).toMatchObject({
      credentialId: 'c1',
      connectKind: 'oauth',
    })
  })

  it('renders the card only for the native payload, not for a tool named auth_required', () => {
    const [call] = standardInterruptToToolCalls({
      interrupt_id: 'int-2',
      action_requests: [{ name: 'auth_required', args: { items: value.items } }],
      review_configs: [{ action_name: 'auth_required', allowed_decisions: ['respond'] }],
    })
    expect(call.name).toBe('request_approval')
  })

  it('keeps a still-broken retry renderable as a new card under the same interrupt id', () => {
    const first = standardPayloadFromInterrupt({ id: 'int-1', value: { ...value, attempt: 1 } })
    const second = standardPayloadFromInterrupt({ id: 'int-1', value: { ...value, attempt: 2 } })
    if (!first || !second) throw new Error('auth_required payload was not recognized')
    const resolved = resolvedInterruptToolCallsFromDecisions(first, [
      { type: 'respond', message: 'retry' },
    ])

    expect(activeInterruptPayloads([first], [], resolved)).toEqual([])
    expect(activeInterruptPayloads([second], [], resolved)).toEqual([second])
    const [firstCall] = standardInterruptToToolCalls(first)
    const [secondCall] = standardInterruptToToolCalls(second)
    expect(secondCall.id).not.toBe(firstCall.id)
    expect(secondCall.args.hitl_interrupt_id).toBe('int-1') // resume still targets the real id
  })

  it('parses items defensively (streaming or malformed args never throw)', () => {
    expect(authRequiredItems(value)).toEqual([
      {
        resourceName: 'Slack',
        toolNames: ['post_message', 'read_channel'],
        fixableBy: 'me',
        credentialId: 'c1',
        connectKind: 'oauth',
      },
      {
        resourceName: 'Jira',
        toolNames: ['create_issue'],
        fixableBy: 'owner',
        credentialId: null,
        connectKind: null,
      },
    ])
    expect(authRequiredItems({ items: '[{"resource' })).toEqual([])
    expect(authRequiredItems({ items: [null, { tool_names: 'x' }] })).toEqual([])
    expect(authRequiredItems(undefined)).toEqual([])
  })

  // G-05: legacy SSE / AG-UI / reconnect synthesis carry the raw value with its kind.
  it('normalizes the discriminated wire payload of the legacy and reconnect paths', () => {
    const wire = {
      ...value,
      interrupt_id: 'int-3',
      type: 'auth_required' as const,
      attempt: 2,
      allowed_decisions: ['respond' as const],
    }
    const payload = toStandardInterruptPayload(wire)
    expect(payload).toMatchObject({ interrupt_id: 'int-3', native: 'auth_required' })
    const [call] = standardInterruptToToolCalls(payload)
    expect(call).toMatchObject({ id: 'int-3:0#2', name: 'moldy_auth_required' })
    expect(call.args).toMatchObject({ attempt: 2, hitl_interrupt_id: 'int-3' })

    const standard = {
      interrupt_id: 'int-4',
      action_requests: [{ name: 'execute_in_skill', args: {} }],
      review_configs: [{ action_name: 'execute_in_skill', allowed_decisions: ['approve'] }],
    } satisfies Parameters<typeof toStandardInterruptPayload>[0]
    expect(toStandardInterruptPayload(standard)).toBe(standard)
  })

  it('keeps the newest attempt when reload state and a replayed stream disagree', () => {
    // The thread state says attempt 2 (reload); a replay still carries attempt 1.
    const payloads = standardPayloadsFromInterrupts([
      { id: 'int-1', value: { ...value, attempt: 2 } },
      { id: 'int-1', value: { ...value, attempt: 1 } },
    ])
    expect(payloads).toHaveLength(1)
    expect(payloads[0]?.action_requests[0]?.args.attempt).toBe(2)
  })

  it('maps the card actions to respond decisions carrying the answered attempt', () => {
    expect(authRequiredDecision('retry', 2)).toEqual({
      type: 'respond',
      message: 'retry',
      attempt: 2,
    })
    expect(authRequiredDecision('cancel', undefined)).toEqual({
      type: 'respond',
      message: 'cancel',
    })
  })
})
