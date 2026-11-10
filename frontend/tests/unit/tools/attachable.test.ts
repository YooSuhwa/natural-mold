import { attachable } from '@/lib/tools/attachable'

describe('attachable', () => {
  const rows = [
    { id: 'old' },
    { id: 'ok', registration_state: 'approved' as const },
    { id: 'waiting', registration_state: 'pending' as const },
    { id: 'refused', registration_state: 'rejected' as const },
  ]

  it('leaves out rows the administrators have not approved', () => {
    expect(attachable(rows).map((row) => row.id)).toEqual(['old', 'ok'])
  })

  it('keeps an unapproved row that is already attached, so it can be removed', () => {
    expect(attachable(rows, new Set(['refused'])).map((row) => row.id)).toEqual([
      'old',
      'ok',
      'refused',
    ])
  })
})
