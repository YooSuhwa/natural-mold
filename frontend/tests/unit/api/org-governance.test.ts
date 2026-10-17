import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }))

vi.mock('@/lib/api/client', () => ({ apiFetch }))

import { orgGovernanceApi } from '@/lib/api/org-governance'
import { isAmount, toAmount } from '@/features/organizations/lib/amount'

const V = '2026-10-16T01:02:03Z'
const IF_MATCH = { 'If-Match': `"${V}"` }

describe('orgGovernanceApi', () => {
  beforeEach(() => apiFetch.mockReset())

  it('puts retention with the organization version', async () => {
    const body = { conversation_days: null, agent_trash_days: 30, removed_member_days: 30 }
    await orgGovernanceApi.putRetention('org 1', body, V)
    expect(apiFetch).toHaveBeenCalledWith('/api/orgs/org%201/retention', {
      method: 'PUT',
      headers: IF_MATCH,
      body: JSON.stringify(body),
    })
  })

  it('addresses a member limit by scope, id and organization', async () => {
    await orgGovernanceApi.putUserLimit('org-1', 'u1', '30', V)
    expect(apiFetch).toHaveBeenCalledWith('/api/usage-limits?scope=user&id=u1&org_id=org-1', {
      method: 'PUT',
      headers: IF_MATCH,
      body: JSON.stringify({ monthly_spend_limit: '30' }),
    })
  })

  it('previews catalog impact with type and id', async () => {
    await orgGovernanceApi.catalogImpact('org-1', 'mcp_server', 's1')
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/orgs/org-1/catalog-policy/impact?type=mcp_server&id=s1',
    )
  })

  it('creates, pages and resends invitations without a version header', async () => {
    await orgGovernanceApi.createInvitation('org-1', { email: 'a@x.io', role: 'member' })
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/invitations', {
      method: 'POST',
      body: JSON.stringify({ email: 'a@x.io', role: 'member' }),
    })
    await orgGovernanceApi.listInvitations('org-1', { cursor: 'c2' })
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/invitations?cursor=c2')
    await orgGovernanceApi.resendInvitation('org-1', 'i1')
    expect(apiFetch).toHaveBeenLastCalledWith('/api/orgs/org-1/invitations/i1/resend', {
      method: 'POST',
    })
  })
})

describe('amount helpers', () => {
  it('accepts non-negative decimals with up to 8 places', () => {
    expect(isAmount('12.5')).toBe(true)
    expect(isAmount('-1')).toBe(false)
    expect(isAmount('1.123456789')).toBe(false)
    expect(toAmount(' 20.50 ')).toBe('20.5')
    expect(toAmount('')).toBeNull()
  })
})
