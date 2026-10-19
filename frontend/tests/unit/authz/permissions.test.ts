import { agentCan, agentPermissions, hasPermission } from '@/lib/authz/permissions'

describe('hasPermission (plan 13.1 권한 게이트)', () => {
  it('answers from my_permissions when the server sent them', () => {
    const source = { myPermissions: ['can_use', 'can_view'], ownerId: 'me' }
    expect(hasPermission(source, 'can_view', 'me')).toBe(true)
    expect(hasPermission(source, 'can_edit', 'me')).toBe(false)
  })

  it('falls back to the legacy owner rule', () => {
    expect(hasPermission({ ownerId: 'me' }, 'can_edit', 'me')).toBe(true)
    expect(hasPermission({ ownerId: 'other' }, 'can_edit', 'me')).toBe(false)
    expect(hasPermission({ ownerId: null }, 'can_edit', 'me')).toBe(false)
    expect(hasPermission({}, 'can_edit', 'me')).toBe(true)
    expect(hasPermission({ isSystem: true }, 'can_use', 'me')).toBe(true)
    expect(hasPermission({ isSystem: true }, 'can_edit', 'me')).toBe(false)
  })
})

describe('agent permissions', () => {
  it('treats a runner view as run-only and legacy bodies as unrestricted', () => {
    expect(agentPermissions({ view: 'runner' })).toEqual(['can_run'])
    expect(agentCan({ view: 'runner' }, 'can_view_config')).toBe(false)
    expect(agentCan({}, 'can_edit')).toBe(true)
    expect(agentCan({ my_permissions: ['can_run', 'can_view_config'] }, 'can_edit')).toBe(false)
  })
})
