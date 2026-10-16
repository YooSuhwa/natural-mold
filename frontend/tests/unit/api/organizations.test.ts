import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('@/lib/api/client', () => ({ apiFetch }))

import {
  meOrganizationsApi,
  orgAdminApi,
  organizationVersionHeader,
  tenantOrganizationsApi,
} from '@/lib/api/organizations'

const UPDATED_AT = '2026-10-16T01:02:03.456789Z'

describe('meOrganizationsApi', () => {
  beforeEach(() => apiFetch.mockReset())

  it('lists my organizations', async () => {
    apiFetch.mockResolvedValue([])
    await meOrganizationsApi.list()
    expect(apiFetch).toHaveBeenCalledWith('/api/me/organizations')
  })

  it('posts the selected org id to switch the active organization', async () => {
    apiFetch.mockResolvedValue({ org_id: 'org-2', tenant_id: 'tenant-1' })
    const result = await meOrganizationsApi.setActive('org-2')
    expect(apiFetch).toHaveBeenCalledWith('/api/me/active-org', {
      method: 'POST',
      body: JSON.stringify({ org_id: 'org-2' }),
    })
    expect(result.org_id).toBe('org-2')
  })
})

describe('tenantOrganizationsApi', () => {
  beforeEach(() => apiFetch.mockReset())

  it('quotes updated_at like the backend ETag', () => {
    expect(organizationVersionHeader(UPDATED_AT)).toEqual({ 'If-Match': `"${UPDATED_AT}"` })
  })

  it('lists with encoded tenant path and paging params', async () => {
    apiFetch.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
    await tenantOrganizationsApi.list('tenant/1', { cursor: 'abc', limit: 20 })
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/tenants/tenant%2F1/organizations?cursor=abc&limit=20',
    )
    await tenantOrganizationsApi.list('tenant-1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/tenants/tenant-1/organizations')
  })

  it('creates without a version header', async () => {
    await tenantOrganizationsApi.create('tenant-1', { name: 'Sales' })
    expect(apiFetch).toHaveBeenCalledWith('/api/tenants/tenant-1/organizations', {
      method: 'POST',
      body: JSON.stringify({ name: 'Sales' }),
    })
  })

  it('sends If-Match on rename, archive and restore', async () => {
    const ifMatch = { 'If-Match': `"${UPDATED_AT}"` }
    await tenantOrganizationsApi.rename('tenant-1', 'org-1', { name: 'New' }, UPDATED_AT)
    expect(apiFetch).toHaveBeenLastCalledWith('/api/tenants/tenant-1/organizations/org-1', {
      method: 'PATCH',
      headers: ifMatch,
      body: JSON.stringify({ name: 'New' }),
    })
    await tenantOrganizationsApi.archive('tenant-1', 'org-1', { confirm_name: 'New' }, UPDATED_AT)
    expect(apiFetch).toHaveBeenLastCalledWith('/api/tenants/tenant-1/organizations/org-1/archive', {
      method: 'POST',
      headers: ifMatch,
      body: JSON.stringify({ confirm_name: 'New' }),
    })
    await tenantOrganizationsApi.restore('tenant-1', 'org-1', UPDATED_AT)
    expect(apiFetch).toHaveBeenLastCalledWith('/api/tenants/tenant-1/organizations/org-1/restore', {
      method: 'POST',
      headers: ifMatch,
    })
  })
})

describe('orgAdminApi', () => {
  beforeEach(() => apiFetch.mockReset())
  const ifMatch = { 'If-Match': '"v1"' }

  it('reads org info and paged directories with encoded ids', async () => {
    await orgAdminApi.get('org/1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org%2F1')
    await orgAdminApi.listMembers('org-1', { cursor: 'c1', limit: 10 })
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/members?cursor=c1&limit=10')
    await orgAdminApi.listGroupMembers('org-1', 'g/1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/groups/g%2F1/members')
  })

  it('sends the org version on every write', async () => {
    await orgAdminApi.update('org-1', { name: 'N' }, 'v1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1', {
      method: 'PATCH',
      headers: ifMatch,
      body: JSON.stringify({ name: 'N' }),
    })
    await orgAdminApi.setMemberStatus('org-1', 'u1', 'suspended', 'v1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/members/u1', {
      method: 'PATCH',
      headers: ifMatch,
      body: JSON.stringify({ status: 'suspended' }),
    })
    await orgAdminApi.removeMember('org-1', 'u1', 'v1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/members/u1', {
      method: 'DELETE',
      headers: ifMatch,
    })
    await orgAdminApi.replaceRoles(
      'org-1',
      { principal: { type: 'group', id: 'g1' }, roles: ['builder'] },
      'v1',
    )
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/roles', {
      method: 'PUT',
      headers: ifMatch,
      body: JSON.stringify({ principal: { type: 'group', id: 'g1' }, roles: ['builder'] }),
    })
    await orgAdminApi.removeGroupMember('org-1', 'g1', 'u1', 'v1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/groups/g1/members/u1', {
      method: 'DELETE',
      headers: ifMatch,
    })
  })

  it('filters grant lists to one principal', async () => {
    await orgAdminApi.listRoles('org-1', { principal: { type: 'group', id: 'g1' }, cursor: 'c2' })
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/orgs/org-1/roles?principal_type=group&principal_id=g1&cursor=c2',
    )
    await orgAdminApi.listCapabilities('org-1', {
      principal: { type: 'organization', id: 'org-1' },
      limit: 200,
    })
    expect(apiFetch).toHaveBeenLastCalledWith(
      '/api/orgs/org-1/capabilities?principal_type=organization&principal_id=org-1&limit=200',
    )
  })

  it('searches principals with a type filter', async () => {
    await orgAdminApi.searchPrincipals('org-1', 'kim', ['user', 'group'])
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/principals?q=kim&types=user%2Cgroup')
  })
})
