import { describe, expect, it } from 'vitest'
import { parseExcludedCapabilities, protocolExcludedCapabilities } from '../excluded-capabilities'

const PAYLOAD = {
  items: [
    { type: 'skill', reason: 'broken', count: 1 },
    { type: 'tool', reason: 'denied', count: 2 },
  ],
  total: 3,
}

describe('excluded capabilities parsing (F-06 / 13.6)', () => {
  it('reads the v3 custom event payload', () => {
    const groups = protocolExcludedCapabilities({
      method: 'custom',
      event_id: 'run-1:excluded_capabilities',
      params: { data: { name: 'moldy.excluded_capabilities', payload: PAYLOAD } },
    })
    expect(groups).toEqual(PAYLOAD.items)
  })

  it('accepts the custom:<name> method form', () => {
    expect(
      protocolExcludedCapabilities({
        method: 'custom:moldy.excluded_capabilities',
        params: { data: { payload: PAYLOAD } },
      }),
    ).toEqual(PAYLOAD.items)
  })

  it('ignores other custom events and invalid items', () => {
    expect(
      protocolExcludedCapabilities({
        method: 'custom',
        params: { data: { name: 'moldy.subagent_names', payload: PAYLOAD } },
      }),
    ).toBeNull()
    expect(
      parseExcludedCapabilities({
        items: [
          { type: 'tool', reason: 'other', count: 1 },
          { type: '', reason: 'denied', count: 1 },
          { type: 'tool', reason: 'denied', count: 0 },
          'x',
        ],
      }),
    ).toBeNull()
    expect(parseExcludedCapabilities({ items: [] })).toBeNull()
  })
})
