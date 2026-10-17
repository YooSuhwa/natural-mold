'use client'

import { useTranslations } from 'next-intl'

import { Checkbox } from '@/components/ui/checkbox'
import { useTenantOrganizations } from '@/lib/hooks/use-organizations'
import { isTenantOrganizationAdminPage } from '@/lib/types/organization'

interface OrgMultiSelectProps {
  tenantId: string
  legend: string
  /** `null` = every organization (offered only when `allowAll`). */
  value: string[] | null
  onChange: (next: string[] | null) => void
  allowAll?: boolean
}

/** "허용 조직" picker for company models and shared credentials (S07, 7.2). */
export function OrgMultiSelect({
  tenantId,
  legend,
  value,
  onChange,
  allowAll = false,
}: OrgMultiSelectProps) {
  const t = useTranslations('orgAdmin.companySettings.orgSelect')
  const page = useTenantOrganizations(tenantId, { limit: 200 })
  const orgs =
    page.data && isTenantOrganizationAdminPage(page.data)
      ? page.data.items.filter((org) => org.status !== 'archived')
      : []
  const all = value === null

  function toggle(orgId: string, checked: boolean) {
    const current = value ?? []
    onChange(checked ? [...current, orgId] : current.filter((id) => id !== orgId))
  }

  return (
    <fieldset className="space-y-2">
      <legend className="moldy-ui-label text-foreground">{legend}</legend>
      {allowAll ? (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={all} onCheckedChange={(checked) => onChange(checked ? null : [])} />
          {t('all')}
        </label>
      ) : null}
      {page.isLoading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : orgs.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {orgs.map((org) => (
            <label key={org.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={all || (value ?? []).includes(org.id)}
                disabled={all}
                onCheckedChange={(checked) => toggle(org.id, checked)}
              />
              {org.name}
            </label>
          ))}
        </div>
      )}
    </fieldset>
  )
}

/** Names for a list cell; `null` reads as every organization. */
export function useOrgNames(tenantId: string): (ids: string[] | null) => string {
  const t = useTranslations('orgAdmin.companySettings.orgSelect')
  const page = useTenantOrganizations(tenantId, { limit: 200 })
  const names = new Map(
    page.data && isTenantOrganizationAdminPage(page.data)
      ? page.data.items.map((org) => [org.id, org.name] as const)
      : [],
  )
  return (ids) => {
    if (ids === null) return t('all')
    if (ids.length === 0) return t('none')
    return ids.map((id) => names.get(id) ?? t('unknown')).join(', ')
  }
}
