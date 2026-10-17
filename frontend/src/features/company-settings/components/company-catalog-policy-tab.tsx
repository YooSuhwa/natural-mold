'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import {
  useTenantCatalogPolicy,
  useUpdateTenantCatalogPolicy,
} from '@/lib/hooks/use-tenant-catalog'
import type { CatalogKind, TenantCatalogItem, TenantCatalogPolicy } from '@/lib/types/tenant-admin'

const KINDS: readonly CatalogKind[] = ['model', 'tool', 'skill', 'mcp_server']
const itemKey = (item: { type: CatalogKind; id: string }) => `${item.type}:${item.id}`

/**
 * S07 company catalog tier (E-07): platform → company → organization narrowing.
 * An item the platform removed cannot be turned on here; organizations can only narrow further.
 */
export function CompanyCatalogPolicyTab({ tenantId }: { tenantId: string }) {
  const policy = useTenantCatalogPolicy(tenantId)
  if (policy.isLoading) return <Skeleton className="h-32 w-full" />
  if (!policy.data) {
    return <ApiErrorState error={policy.error} onRetry={() => void policy.refetch()} />
  }
  return <CatalogPolicyForm key={policy.data.updated_at} tenantId={tenantId} policy={policy.data} />
}

function CatalogPolicyForm({
  tenantId,
  policy,
}: {
  tenantId: string
  policy: TenantCatalogPolicy
}) {
  const t = useTranslations('orgAdmin.companySettings.catalog')
  const errorMessage = useOrgErrorMessage()
  const update = useUpdateTenantCatalogPolicy(tenantId)
  const [enabled, setEnabled] = useState(
    () => new Map(policy.items.map((item) => [itemKey(item), item.company_enabled])),
  )
  const isOn = (item: TenantCatalogItem) => enabled.get(itemKey(item)) ?? item.company_enabled
  const dirty = policy.items.some((item) => isOn(item) !== item.company_enabled)

  function save() {
    // Overrides only: an absent item stays on, and enabling a platform-hidden item is refused.
    const items = policy.items
      .filter((item) => !isOn(item))
      .map((item) => ({ type: item.type, id: item.id, enabled: false }))
    update.mutate(
      { items, version: policy.updated_at },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={t('title')}
      description={policy.allow_system_models ? t('description') : t('platformDisabled')}
      actions={
        <Button size="sm" disabled={!dirty || update.isPending} onClick={save}>
          {t('save')}
        </Button>
      }
    >
      {policy.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <div className="space-y-5">
          {KINDS.map((kind) => {
            const items = policy.items.filter((item) => item.type === kind)
            if (items.length === 0) return null
            return (
              <div key={kind} className="space-y-2">
                <h3 className="moldy-ui-label text-foreground">{t(`kind.${kind}`)}</h3>
                <ul className="divide-y divide-border">
                  {items.map((item) => (
                    <li key={itemKey(item)} className="flex items-center gap-3 py-2">
                      <span className="min-w-0 flex-1 text-sm">{item.name}</span>
                      <Badge variant={item.source === 'company' ? 'secondary' : 'outline'}>
                        {t(`source.${item.source}`)}
                      </Badge>
                      {item.platform_available ? null : (
                        <Badge variant="outline">{t('platformHidden')}</Badge>
                      )}
                      <Switch
                        checked={item.platform_available && isOn(item)}
                        disabled={!item.platform_available}
                        aria-label={t('toggle', { name: item.name })}
                        onCheckedChange={(checked) =>
                          setEnabled((prev) => new Map(prev).set(itemKey(item), checked))
                        }
                      />
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </div>
      )}
    </SettingsSectionCard>
  )
}
