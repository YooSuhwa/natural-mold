import { tenantVersionHeader } from '@/lib/api/tenant-admin'
import { companyNavItems } from '@/components/layout/company-nav-items'

describe('tenantVersionHeader', () => {
  it('echoes the body timestamp in the backend ETag form', () => {
    expect(tenantVersionHeader('2026-10-18T01:02:03.456789Z')).toEqual({
      'If-Match': '"2026-10-18T01:02:03.456789+00:00"',
    })
  })
})

describe('companyNavItems', () => {
  const t = (key: string) => key
  const hrefs = (access: Parameters<typeof companyNavItems>[0]) =>
    companyNavItems(access, t).map((item) => item.href)

  it('shows nothing until the company authority is known', () => {
    expect(hrefs(undefined)).toEqual([])
  })

  it('narrows entries by company role', () => {
    expect(hrefs('member')).toEqual(['/settings/company/policies'])
    expect(hrefs('auditor')).toEqual(['/settings/company', '/settings/company/policies'])
    expect(hrefs('admin')).toEqual([
      '/settings/company',
      '/settings/company/members',
      '/settings/company/policies',
      '/settings/company/models',
      '/settings/company/sso',
    ])
  })
})
