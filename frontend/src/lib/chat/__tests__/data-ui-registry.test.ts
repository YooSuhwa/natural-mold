import { describe, expect, it } from 'vitest'
import { DATA_UI_REGISTRY, MOLDY_UI_DATA_PART_NAME, resolveDataUI } from '../data-ui-registry'

describe('resolveDataUI', () => {
  it('resolves a valid demo_note payload to a component + parsed props', () => {
    const resolved = resolveDataUI('demo_note', { text: 'hello' })

    expect(resolved).not.toBeNull()
    expect(resolved?.props).toEqual({ text: 'hello' })
    expect(typeof resolved?.Component).toBe('function')
  })

  it('returns null for an unknown type (fail-safe)', () => {
    expect(resolveDataUI('not_a_registered_type', { text: 'hello' })).toBeNull()
  })

  it('returns null when props fail Zod validation (fail-safe)', () => {
    expect(resolveDataUI('demo_note', { text: 123 })).toBeNull()
    expect(resolveDataUI('demo_note', {})).toBeNull()
    expect(resolveDataUI('demo_note', null)).toBeNull()
    expect(resolveDataUI('demo_note', 'not an object')).toBeNull()
  })

  it('registers the demo_note type and the data-part name', () => {
    expect(Object.keys(DATA_UI_REGISTRY)).toContain('demo_note')
    expect(MOLDY_UI_DATA_PART_NAME).toBe('moldy_ui')
  })

  it('resolves a valid data_table payload', () => {
    const resolved = resolveDataUI('data_table', {
      title: 'T',
      searchable: true,
      columns: [{ key: 'a', header: 'A' }],
      rows: [{ a: 1 }, { a: 2 }],
    })
    expect(resolved).not.toBeNull()
    expect((resolved?.props as { columns: unknown[] }).columns).toHaveLength(1)
    expect(typeof resolved?.Component).toBe('function')
  })

  it('returns null when data_table props fail validation (fail-safe)', () => {
    // columns missing header
    expect(resolveDataUI('data_table', { columns: [{ key: 'a' }], rows: [] })).toBeNull()
    // rows not an array
    expect(resolveDataUI('data_table', { columns: [], rows: {} })).toBeNull()
    expect(resolveDataUI('data_table', {})).toBeNull()
  })

  it('resolves a valid chart payload', () => {
    const resolved = resolveDataUI('chart', {
      chartType: 'line',
      series: [{ label: 'a', value: 1 }],
      title: 'T',
    })
    expect(resolved).not.toBeNull()
    expect((resolved?.props as { chartType: string }).chartType).toBe('line')
  })

  it('returns null when chart props fail validation (fail-safe)', () => {
    // invalid chartType
    expect(resolveDataUI('chart', { chartType: 'pie', series: [] })).toBeNull()
    // non-numeric value
    expect(
      resolveDataUI('chart', { chartType: 'bar', series: [{ label: 'a', value: 'x' }] }),
    ).toBeNull()
    expect(resolveDataUI('chart', {})).toBeNull()
  })

  it('resolves a valid stats payload (string or number value)', () => {
    const resolved = resolveDataUI('stats', {
      items: [
        { label: 'a', value: 10, delta: 5 },
        { label: 'b', value: 'ok' },
      ],
    })
    expect(resolved).not.toBeNull()
    expect((resolved?.props as { items: unknown[] }).items).toHaveLength(2)
  })

  it('returns null when stats props fail validation (fail-safe)', () => {
    // item missing value
    expect(resolveDataUI('stats', { items: [{ label: 'a' }] })).toBeNull()
    // delta not a number
    expect(resolveDataUI('stats', { items: [{ label: 'a', value: 1, delta: 'x' }] })).toBeNull()
    expect(resolveDataUI('stats', {})).toBeNull()
  })

  it('resolves a valid terminal payload (string or array lines)', () => {
    expect(resolveDataUI('terminal', { lines: 'one line' })).not.toBeNull()
    const resolved = resolveDataUI('terminal', {
      lines: ['a', 'b'],
      command: 'ls',
      exitCode: 0,
    })
    expect(resolved).not.toBeNull()
    expect((resolved?.props as { lines: string[] }).lines).toHaveLength(2)
  })

  it('returns null when terminal props fail validation (fail-safe)', () => {
    // lines wrong type
    expect(resolveDataUI('terminal', { lines: 123 })).toBeNull()
    // exitCode not a number
    expect(resolveDataUI('terminal', { lines: 'x', exitCode: 'nope' })).toBeNull()
    expect(resolveDataUI('terminal', {})).toBeNull()
  })
})
