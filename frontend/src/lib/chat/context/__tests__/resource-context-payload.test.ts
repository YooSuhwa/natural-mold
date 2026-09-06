import { describe, expect, it } from 'vitest'

import { withResourceContextRunInput } from '../resource-context-payload'

describe('resource context queue payload', () => {
  it('preserves exact draft refs in the authoritative run input across queueing', () => {
    const artifact = {
      kind: 'artifact',
      id: crypto.randomUUID(),
      version_id: crypto.randomUUID(),
      label: 'Pinned version',
    } as const

    expect(
      withResourceContextRunInput(
        { messages: [{ role: 'user', content: 'Review this' }] },
        { resource_context: [artifact], draft_marker: 'keep' },
      ),
    ).toEqual({
      kind: 'valid',
      input: {
        messages: [{ role: 'user', content: 'Review this' }],
        resource_context: [artifact],
      },
      refs: [artifact],
    })
  })

  it('rejects dirty refs and browser attempts to submit the server snapshot key', () => {
    expect(
      withResourceContextRunInput(
        { messages: [], _moldy_resource_context_v1: { text: 'stolen' } },
        undefined,
      ),
    ).toEqual({ kind: 'invalid', reason: 'reserved-server-field' })
    expect(
      withResourceContextRunInput(
        { messages: [] },
        { resource_context: [{ kind: 'file', id: crypto.randomUUID(), url: 'file:///tmp/a' }] },
      ),
    ).toEqual({ kind: 'invalid', reason: 'malformed' })
    expect(withResourceContextRunInput({ messages: [] }, { resource_context: [] })).toEqual({
      kind: 'invalid',
      reason: 'malformed',
    })
  })
})
