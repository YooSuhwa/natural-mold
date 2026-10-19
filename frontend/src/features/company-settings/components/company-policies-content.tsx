'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useOrgErrorMessage } from '@/features/organizations/lib/error-message'
import { useTenantPolicies, useUpdateTenantPolicies } from '@/lib/hooks/use-tenant-admin'
import type { TenantPolicies, TenantPublishPolicy } from '@/lib/types/tenant-admin'
import { CompanyAdminGate, type CompanyScope } from './company-admin-gate'
import { CompanyPublicationRequests } from './company-publication-requests'

const PUBLISH_POLICIES: readonly TenantPublishPolicy[] = ['disabled', 'open', 'approval']
const MIN_RETENTION = 90
const MAX_RETENTION = 3650

/** S05 "정책" and "공개 승인": company members read, company admins edit and decide. */
export function CompanyPoliciesContent() {
  const t = useTranslations('orgAdmin.companySettings.policies')
  return (
    <CompanyAdminGate title={t('title')} description={t('description')}>
      {(scope) => <PoliciesBody scope={scope} />}
    </CompanyAdminGate>
  )
}

function PoliciesBody({ scope }: { scope: CompanyScope }) {
  const policies = useTenantPolicies(scope.tenantId)
  if (policies.isLoading) return <Skeleton className="h-32 w-full" />
  if (!policies.data) {
    return <ApiErrorState error={policies.error} onRetry={() => void policies.refetch()} />
  }
  return (
    <div className="space-y-4">
      <PolicyForm key={policies.data.updated_at} scope={scope} policies={policies.data} />
      {scope.isAdmin && policies.data.marketplace_tenant_publish === 'approval' ? (
        <CompanyPublicationRequests tenantId={scope.tenantId} />
      ) : null}
    </div>
  )
}

function PolicyForm({ scope, policies }: { scope: CompanyScope; policies: TenantPolicies }) {
  const t = useTranslations('orgAdmin.companySettings.policies')
  const errorMessage = useOrgErrorMessage()
  const update = useUpdateTenantPolicies(scope.tenantId)
  const [publish, setPublish] = useState(policies.marketplace_tenant_publish)
  const [retention, setRetention] = useState(String(policies.audit_retention_days))
  const [pendingDelete, setPendingDelete] = useState<number | null>(null)
  const days = Number(retention)
  const retentionValid = Number.isInteger(days) && days >= MIN_RETENTION && days <= MAX_RETENTION
  const dirty =
    publish !== policies.marketplace_tenant_publish || days !== policies.audit_retention_days
  const readOnly = !scope.isAdmin

  function save(confirm: boolean) {
    update.mutate(
      {
        data: { marketplace_tenant_publish: publish, audit_retention_days: days, confirm },
        version: policies.updated_at,
      },
      {
        onSuccess: (result) => {
          if (result.preview && !confirm) {
            setPendingDelete(result.preview.audit_events)
            return
          }
          setPendingDelete(null)
          toast.success(t('saved'))
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={t('sectionTitle')}
      description={readOnly ? t('readOnly') : t('sectionDescription')}
    >
      <div className="space-y-5">
        <fieldset className="space-y-2">
          <legend className="moldy-ui-label text-foreground">{t('publish.label')}</legend>
          <p className="text-sm text-muted-foreground">{t('publish.description')}</p>
          <div className="flex flex-wrap gap-2">
            {PUBLISH_POLICIES.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={publish === option ? 'default' : 'outline'}
                aria-pressed={publish === option}
                disabled={readOnly}
                onClick={() => setPublish(option)}
              >
                {t(`publish.${option}`)}
              </Button>
            ))}
          </div>
        </fieldset>
        <FormFieldShell
          id="audit-retention"
          label={t('retention.label')}
          description={t('retention.description', { min: MIN_RETENTION, max: MAX_RETENTION })}
          error={retentionValid ? null : t('retention.invalid')}
        >
          <Input
            id="audit-retention"
            type="number"
            min={MIN_RETENTION}
            max={MAX_RETENTION}
            value={retention}
            disabled={readOnly}
            onChange={(event) => setRetention(event.target.value)}
          />
        </FormFieldShell>
        {readOnly ? null : (
          <Button
            disabled={!dirty || !retentionValid || update.isPending}
            onClick={() => save(false)}
          >
            {t('save')}
          </Button>
        )}
      </div>
      <DeleteConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => (open ? undefined : setPendingDelete(null))}
        title={t('confirmTitle')}
        description={t('confirmDescription', { count: pendingDelete ?? 0 })}
        confirmLabel={t('confirm')}
        isPending={update.isPending}
        onConfirm={() => save(true)}
      />
    </SettingsSectionCard>
  )
}
