'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useRenameTenant } from '@/lib/hooks/use-tenant-admin'
import { CompanyAdminGate, type CompanyScope } from './company-admin-gate'

/** S05 company overview: profile, counts and (company admin) rename. */
export function CompanyProfileContent() {
  const t = useTranslations('orgAdmin.companySettings.profile')
  return (
    <CompanyAdminGate title={t('title')} description={t('description')}>
      {(scope) => <CompanyProfileCard key={scope.overview.updated_at} scope={scope} />}
    </CompanyAdminGate>
  )
}

function CompanyProfileCard({ scope }: { scope: CompanyScope }) {
  const t = useTranslations('orgAdmin.companySettings.profile')
  const errorMessage = useOrgErrorMessage()
  const { overview, isAdmin, tenantId } = scope
  const rename = useRenameTenant(tenantId)
  const [name, setName] = useState(overview.name)
  const trimmed = name.trim()
  const stats = [
    { key: 'organizations', value: overview.organization_count },
    { key: 'members', value: overview.active_member_count },
    { key: 'admins', value: overview.active_admin_count },
  ] as const

  return (
    <SettingsSectionCard
      title={overview.name}
      description={t(`access.${overview.access}`)}
      actions={<Badge variant="secondary">{t(`status.${overview.status}`)}</Badge>}
    >
      <div className="space-y-4">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {stats.map((stat) =>
            stat.value === undefined ? null : (
              <div key={stat.key} className="moldy-muted-panel p-3">
                <dt className="moldy-ui-caption text-muted-foreground">{t(stat.key)}</dt>
                <dd className="text-lg font-semibold tabular-nums">{stat.value}</dd>
              </div>
            ),
          )}
        </dl>
        <p className="text-sm text-muted-foreground">{t('slug', { slug: overview.slug })}</p>
        {isAdmin ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <FormFieldShell id="company-name" label={t('name')} className="flex-1">
              <Input
                id="company-name"
                value={name}
                maxLength={200}
                onChange={(event) => setName(event.target.value)}
              />
            </FormFieldShell>
            <Button
              disabled={rename.isPending || trimmed.length === 0 || trimmed === overview.name}
              onClick={() =>
                rename.mutate(
                  { name: trimmed, version: overview.updated_at },
                  {
                    onSuccess: () => toast.success(t('saved')),
                    onError: (err) => toast.error(errorMessage(err)),
                  },
                )
              }
            >
              {t('save')}
            </Button>
          </div>
        ) : null}
      </div>
    </SettingsSectionCard>
  )
}
