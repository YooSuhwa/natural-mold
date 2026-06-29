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
})
