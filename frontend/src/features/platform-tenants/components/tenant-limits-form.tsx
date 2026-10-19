'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useUpdatePlatformTenant } from '@/lib/hooks/use-platform-tenants'
import type { PlatformTenant } from '@/lib/types/platform-tenant'
import { toAmount, useTenantErrorMessage } from '../lib/messages'
import { LimitField, parseLimit } from './tenant-limit-field'

const text = (value: number | null) => (value === null ? '' : String(value))

/** S04 한도(수정): name and company limits, saved with the company's version. */
export function TenantLimitsForm({ tenant }: { tenant: PlatformTenant }) {
  const t = useTranslations('appSettings.platformTenants.limits')
  const errorMessage = useTenantErrorMessage()
  const update = useUpdatePlatformTenant(tenant)
  const [name, setName] = useState(tenant.name)
  const [members, setMembers] = useState(text(tenant.limits.member_limit))
  const [spend, setSpend] = useState(text(toAmount(tenant.limits.monthly_spend_limit)))
  const [runs, setRuns] = useState(text(tenant.limits.concurrent_run_limit))
  const [systemModels, setSystemModels] = useState(tenant.limits.allow_system_models)
  const [error, setError] = useState<string | null>(null)
  const parsed = [
    parseLimit(members, 'members'),
    parseLimit(spend, 'spend'),
    parseLimit(runs, 'runs'),
  ]
  const valid = name.trim() !== '' && parsed.every((limit) => limit.valid)

  function save() {
    setError(null)
    update.mutate(
      {
        name: name.trim(),
        member_limit: parsed[0].value,
        monthly_spend_limit: parsed[1].value,
        concurrent_run_limit: parsed[2].value,
        allow_system_models: systemModels,
      },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (err) => {
          const message = errorMessage(err)
          setError(message)
          toast.error(message)
        },
      },
    )
  }

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <div className="space-y-3">
        <FormFieldShell id="tenant-edit-name" label={t('name')} required error={error}>
          <Input
            id="tenant-edit-name"
            value={name}
            maxLength={200}
            onChange={(event) => setName(event.target.value)}
          />
        </FormFieldShell>
        <div className="grid gap-3 sm:grid-cols-3">
          <LimitField
            id="tenant-edit-members"
            kind="members"
            value={members}
            onChange={setMembers}
          />
          <LimitField id="tenant-edit-spend" kind="spend" value={spend} onChange={setSpend} />
          <LimitField id="tenant-edit-runs" kind="runs" value={runs} onChange={setRuns} />
        </div>
        <FormFieldShell
          id="tenant-edit-system-models"
          label={t('systemModels')}
          description={t('systemModelsHint')}
          layout="inline"
        >
          <Switch
            id="tenant-edit-system-models"
            checked={systemModels}
            onCheckedChange={(checked) => setSystemModels(checked)}
          />
        </FormFieldShell>
        <div className="flex justify-end">
          <Button disabled={update.isPending || !valid} onClick={save}>
            {t('save')}
          </Button>
        </div>
      </div>
    </SettingsSectionCard>
  )
}
