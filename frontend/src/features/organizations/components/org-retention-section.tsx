'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useOrgRetention, useSaveOrgRetention } from '@/lib/hooks/use-org-governance'
import type { OrgRetention, OrgRetentionView, RetentionPreview } from '@/lib/types/org-governance'
import { useOrgErrorMessage } from '../lib/error-message'

/** Backend `app/schemas/retention.py` bounds. */
const BOUNDS = {
  conversation_days: [30, 3650],
  agent_trash_days: [7, 90],
  removed_member_days: [7, 3650],
} as const

type DayField = keyof typeof BOUNDS

function inRange(field: DayField, value: string): boolean {
  if (!/^\d+$/.test(value)) return false
  const days = Number(value)
  return days >= BOUNDS[field][0] && days <= BOUNDS[field][1]
}

/** S08 "보존 정책" (`GET|PUT /api/orgs/{id}/retention`); shortening previews deletions first. */
export function OrgRetentionSection({ orgId, canEdit }: { orgId: string; canEdit: boolean }) {
  const t = useTranslations('orgAdmin.settings.retention')
  const retention = useOrgRetention(orgId)
  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      {retention.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : retention.data ? (
        <RetentionForm
          key={retention.data.updated_at}
          orgId={orgId}
          view={retention.data}
          canEdit={canEdit}
        />
      ) : (
        <ApiErrorState error={retention.error} onRetry={() => void retention.refetch()} />
      )}
    </SettingsSectionCard>
  )
}

function RetentionForm({
  orgId,
  view,
  canEdit,
}: {
  orgId: string
  view: OrgRetentionView
  canEdit: boolean
}) {
  const t = useTranslations('orgAdmin.settings.retention')
  const errorMessage = useOrgErrorMessage()
  const save = useSaveOrgRetention(orgId)
  const [unlimited, setUnlimited] = useState(view.conversation_days === null)
  const [values, setValues] = useState<Record<DayField, string>>({
    conversation_days: String(view.conversation_days ?? 365),
    agent_trash_days: String(view.agent_trash_days),
    removed_member_days: String(view.removed_member_days),
  })
  const [preview, setPreview] = useState<RetentionPreview | null>(null)

  const valid =
    (unlimited || inRange('conversation_days', values.conversation_days)) &&
    inRange('agent_trash_days', values.agent_trash_days) &&
    inRange('removed_member_days', values.removed_member_days)
  const policy: OrgRetention = {
    conversation_days: unlimited ? null : Number(values.conversation_days),
    agent_trash_days: Number(values.agent_trash_days),
    removed_member_days: Number(values.removed_member_days),
  }
  const dirty =
    policy.conversation_days !== view.conversation_days ||
    policy.agent_trash_days !== view.agent_trash_days ||
    policy.removed_member_days !== view.removed_member_days

  function submit(confirm: boolean) {
    save.mutate(
      { data: { ...policy, confirm }, version: view.updated_at },
      {
        onSuccess: (result) => {
          if (!result.saved && result.preview) {
            setPreview(result.preview)
            return
          }
          setPreview(null)
          toast.success(t('saved'))
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  const field = (name: DayField) => (
    <FormFieldShell
      id={`retention-${name}`}
      label={t(`${name}.label`)}
      description={t('range', { min: BOUNDS[name][0], max: BOUNDS[name][1] })}
      error={
        !(name === 'conversation_days' && unlimited) && !inRange(name, values[name])
          ? t('outOfRange')
          : null
      }
    >
      <Input
        id={`retention-${name}`}
        inputMode="numeric"
        className="w-full sm:w-40"
        value={values[name]}
        disabled={!canEdit || (name === 'conversation_days' && unlimited)}
        onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))}
      />
    </FormFieldShell>
  )

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {field('conversation_days')}
        <label htmlFor="retention-unlimited" className="flex items-center gap-2 text-sm">
          <Checkbox
            id="retention-unlimited"
            checked={unlimited}
            disabled={!canEdit}
            onCheckedChange={(checked) => setUnlimited(checked === true)}
          />
          {t('unlimited')}
        </label>
      </div>
      {field('agent_trash_days')}
      {field('removed_member_days')}
      {canEdit ? (
        <Button onClick={() => submit(false)} disabled={!valid || !dirty || save.isPending}>
          {t('submit')}
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">{t('readOnly')}</p>
      )}
      <DeleteConfirmDialog
        open={preview !== null}
        onOpenChange={(open) => {
          if (!open) setPreview(null)
        }}
        title={t('confirm.title')}
        description={preview ? t('confirm.description', { ...preview }) : undefined}
        confirmLabel={t('confirm.submit')}
        isPending={save.isPending}
        onConfirm={() => submit(true)}
      />
    </div>
  )
}
