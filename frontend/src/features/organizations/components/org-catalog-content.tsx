'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { CountedTabs } from '@/components/shared/counted-tabs'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useOrgCatalog, useSaveOrgCatalog } from '@/lib/hooks/use-org-governance'
import { useOrganizationInfo } from '@/lib/hooks/use-organizations'
import type { CatalogItemView, CatalogKind, CatalogPolicyView } from '@/lib/types/org-governance'
import type { OrganizationSummary } from '@/lib/types/organization'
import { orgAccess } from '../lib/access'
import { nextOverrides } from '../lib/catalog-overrides'
import { useOrgErrorMessage } from '../lib/error-message'
import { CatalogDisableDialog } from './org-catalog-disable-dialog'
import { OrgPageFrame } from './org-page-frame'

const KINDS: readonly CatalogKind[] = ['model', 'tool', 'skill', 'mcp_server']

/** S09 카탈로그 정책 (`GET|PUT /api/orgs/{id}/catalog-policy`, `can_manage_policy`). */
export function OrgCatalogContent() {
  const t = useTranslations('orgAdmin.catalog')
  return (
    <OrgPageFrame title={t('title')} description={t('description')}>
      {(organization) => <CatalogBody organization={organization} />}
    </OrgPageFrame>
  )
}

function CatalogBody({ organization }: { organization: OrganizationSummary }) {
  const t = useTranslations('orgAdmin.catalog')
  const info = useOrganizationInfo(organization.id)
  const access = orgAccess(organization, info.data?.permissions)
  // Fail closed: nothing is requested until the server confirms `can_manage_policy`.
  const catalog = useOrgCatalog(organization.id, access.canManagePolicy && !!info.data)

  if (info.isLoading) return <Skeleton className="h-32 w-full" />
  if (!info.data) return <ApiErrorState error={info.error} onRetry={() => void info.refetch()} />
  if (!access.canManagePolicy) {
    return <p className="text-sm text-muted-foreground">{t('noAccess')}</p>
  }
  if (catalog.isLoading) return <Skeleton className="h-32 w-full" />
  if (!catalog.data) {
    return <ApiErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />
  }
  return <CatalogList orgId={organization.id} view={catalog.data} />
}

function CatalogList({ orgId, view }: { orgId: string; view: CatalogPolicyView }) {
  const t = useTranslations('orgAdmin.catalog')
  const errorMessage = useOrgErrorMessage()
  const save = useSaveOrgCatalog(orgId)
  const [kind, setKind] = useState<CatalogKind>('model')
  const [disabling, setDisabling] = useState<CatalogItemView | null>(null)
  const items = view.items.filter((item) => item.type === kind)

  function apply(target: CatalogItemView, enabled: boolean) {
    save.mutate(
      { items: nextOverrides(view.items, target, enabled), version: view.updated_at },
      {
        onSuccess: () => {
          setDisabling(null)
          toast.success(t(enabled ? 'enabled' : 'disabled', { name: target.name }))
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <SettingsSectionCard title={t('listTitle')} description={t('listDescription')}>
      <div className="space-y-3">
        <CountedTabs
          value={kind}
          ariaLabel={t('listTitle')}
          onValueChange={(value) => {
            const match = KINDS.find((option) => option === value)
            if (match) setKind(match)
          }}
          tabs={KINDS.map((value) => ({
            value,
            label: t(`kind.${value}`),
            count: view.items.filter((item) => item.type === value).length,
          }))}
        />
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((item) => {
              const id = `catalog-${item.type}-${item.id}`
              return (
                <li key={id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0 space-y-1">
                    <label htmlFor={id} className="block truncate text-sm font-medium">
                      {item.name}
                    </label>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{t(`source.${item.source}`)}</Badge>
                      {!item.company_enabled ? (
                        <Badge variant="secondary">{t('companyDisabled')}</Badge>
                      ) : null}
                    </div>
                  </div>
                  <Switch
                    id={id}
                    checked={item.enabled}
                    disabled={!item.company_enabled || save.isPending}
                    onCheckedChange={(checked) =>
                      checked ? apply(item, true) : setDisabling(item)
                    }
                  />
                </li>
              )
            })}
          </ul>
        )}
      </div>
      <CatalogDisableDialog
        orgId={orgId}
        item={disabling}
        isPending={save.isPending}
        onCancel={() => setDisabling(null)}
        onConfirm={(item) => apply(item, false)}
      />
    </SettingsSectionCard>
  )
}
