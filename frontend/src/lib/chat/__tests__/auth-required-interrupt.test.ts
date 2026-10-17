import { describe, expect, it } from 'vitest'

import { authRequiredItems } from '@/lib/chat/auth-required-interrupt'
import {
  activeInterruptPayloads,
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadFromInterrupt,
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
      { resourceName: 'Slack', toolNames: ['post_message', 'read_channel'], fixableBy: 'me' },
      { resourceName: 'Jira', toolNames: ['create_issue'], fixableBy: 'owner' },
    ])
    expect(authRequiredItems({ items: '[{"resource' })).toEqual([])
    expect(authRequiredItems({ items: [null, { tool_names: 'x' }] })).toEqual([])
    expect(authRequiredItems(undefined)).toEqual([])
  })
})
