'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useOrgLimits, useSaveMemberDefaultLimit } from '@/lib/hooks/use-org-governance'
import type { UsageLimitView } from '@/lib/types/org-governance'
import { formatDisplayDate } from '@/lib/utils/display-format'
import { isAmount, toAmount, usd } from '../lib/amount'
import { useOrgErrorMessage } from '../lib/error-message'

/**
 * S08 "사용 한도" (H-06, `GET|PUT /api/orgs/{id}/limits`): this month's use against the
 * company-set organization limit (read-only) and the member default monthly limit.
 */
export function OrgLimitsSection({
  orgId,
  version,
  canEdit,
}: {
  orgId: string
  version: string
  canEdit: boolean
}) {
  const t = useTranslations('orgAdmin.settings.limits')
  const limits = useOrgLimits(orgId)
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {limits.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : limits.data ? (
        <div className="space-y-4">
          <p className="text-sm">
            {t('usage', {
              used: usd(limits.data.used) ?? '',
              limit: usd(limits.data.monthly_spend_limit) ?? t('noLimit'),
            })}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('resets', { date: formatDisplayDate(limits.data.resets_at) })}
          </p>
          <MemberDefaultForm
            key={`${version}:${limits.data.default_member_limit ?? ''}`}
            orgId={orgId}
            view={limits.data}
            version={version}
            canEdit={canEdit}
          />
        </div>
      ) : (
        <ApiErrorState error={limits.error} onRetry={() => void limits.refetch()} />
      )}
    </SettingsSectionCard>
  )
}

function MemberDefaultForm({
  orgId,
  view,
  version,
  canEdit,
}: {
  orgId: string
  view: UsageLimitView
  version: string
  canEdit: boolean
}) {
  const t = useTranslations('orgAdmin.settings.limits')
  const errorMessage = useOrgErrorMessage()
  const save = useSaveMemberDefaultLimit(orgId)
  const [value, setValue] = useState(view.default_member_limit ?? '')
  const amount = toAmount(value)
  const cap = view.monthly_spend_limit ?? view.parent_limit
  const overCap = amount !== null && cap !== null && Number(amount) > Number(cap)
  const invalid = (value.trim() !== '' && !isAmount(value)) || overCap
  const dirty = amount !== toAmount(view.default_member_limit)

  return (
    <div className="space-y-3">
      <FormFieldShell
        id="org-member-default-limit"
        label={t('memberDefault')}
        description={cap ? t('memberDefaultCap', { cap: usd(cap) ?? '' }) : t('memberDefaultHint')}
        error={overCap ? t('overCap') : invalid ? t('invalid') : null}
      >
        <Input
          id="org-member-default-limit"
          inputMode="decimal"
          className="w-full sm:w-40"
          placeholder={t('placeholder')}
          value={value}
          disabled={!canEdit}
          onChange={(event) => setValue(event.target.value)}
        />
      </FormFieldShell>
      {canEdit ? (
        <Button
          disabled={invalid || !dirty || save.isPending}
          onClick={() =>
            save.mutate(
              { limit: amount, version },
              {
                onSuccess: () => toast.success(t('saved')),
                onError: (error) => toast.error(errorMessage(error)),
              },
            )
          }
        >
          {t('submit')}
        </Button>
      ) : null}
    </div>
  )
}
