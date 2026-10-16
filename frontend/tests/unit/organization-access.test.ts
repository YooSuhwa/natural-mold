import { describe, expect, it } from 'vitest'

import { orgAccess } from '@/features/organizations/lib/access'
import { groupGrantsByPrincipal } from '@/features/organizations/components/org-roles-content'
import { makeOrganization } from './hooks/organization-fixtures'

describe('orgAccess', () => {
  it('derives admin/auditor gates from summary roles', () => {
    const admin = orgAccess(makeOrganization({ roles: ['admin'] }))
    expect(admin.canManageMembers && admin.canManagePolicy && admin.canReadGrants).toBe(true)
    const auditor = orgAccess(makeOrganization({ roles: ['auditor'] }))
    expect([auditor.canManageMembers, auditor.canReadGrants]).toEqual([false, false])
    const builder = orgAccess(
      makeOrganization({ roles: ['builder'], capabilities: ['cap_create_agent'] }),
    )
    expect([builder.canManageMembers, builder.canReadGrants]).toEqual([false, false])
    expect(builder.hasCapability('cap_create_agent')).toBe(true)
    expect(builder.hasCapability('cap_share_org_wide')).toBe(false)
    expect(orgAccess(null).canManageMembers).toBe(false)
  })

  it('lets server permissions override summary roles (company admins)', () => {
    const companyAdmin = orgAccess(makeOrganization({ roles: [] }), ['can_manage_members'])
    expect([companyAdmin.canManageMembers, companyAdmin.canManagePolicy]).toEqual([true, false])
    expect(orgAccess(makeOrganization({ roles: ['admin'] }), []).canManagePolicy).toBe(false)
  })
})

describe('groupGrantsByPrincipal', () => {
  it('merges one-row-per-grant pages per principal', () => {
    const rows = groupGrantsByPrincipal([
      { principal: { type: 'user', id: 'u1' }, value: 'admin' },
      { principal: { type: 'group', id: 'u1' }, value: 'builder' },
      { principal: { type: 'user', id: 'u1' }, value: 'auditor' },
    ])
    expect(rows).toEqual([
      { principal: { type: 'user', id: 'u1' }, values: ['admin', 'auditor'] },
      { principal: { type: 'group', id: 'u1' }, values: ['builder'] },
    ])
  })
})
