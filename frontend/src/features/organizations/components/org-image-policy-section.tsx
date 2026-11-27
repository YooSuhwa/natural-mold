'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useModels } from '@/lib/hooks/use-models'
import { useOrgCatalog } from '@/lib/hooks/use-org-governance'
import { useUpdateOrganization } from '@/lib/hooks/use-organizations'
import type { OrganizationView, OrgImageDescriptionPolicy } from '@/lib/types/organization'
import { useOrgErrorMessage } from '../lib/error-message'
import { imagePolicyDestination } from '../lib/image-policy-model'

const limits = {
  max_input_bytes: { initial: '4194304', min: 1, max: 8388608, step: 1 },
  max_output_tokens: { initial: '256', min: 1, max: 1024, step: 1 },
  timeout_seconds: { initial: '15', min: 1, max: 60, step: 1 },
  max_cost_usd: { initial: '0.50', min: 0.00000001, max: 10, step: 0.00000001 },
} as const
type Limit = keyof typeof limits
const limitKeys = Object.keys(limits) as Limit[]

export function OrgImagePolicySection({
  view,
  canEdit,
}: {
  view: OrganizationView
  canEdit: boolean
}) {
  const t = useTranslations('orgAdmin.settings.imageDescription')
  const initial = view.settings.image_description ?? null
  const [enabled, setEnabled] = useState(Boolean(initial))
  const update = useUpdateOrganization(view.id)
  const errorMessage = useOrgErrorMessage()
  function save(policy: OrgImageDescriptionPolicy | null) {
    if (!canEdit || update.isPending) return
    update.mutate(
      { data: { settings: { image_description: policy } }, version: view.updated_at },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <div className="space-y-4">
        <FormFieldShell id="org-image-description" label={t('allow')} layout="inline">
          <Switch
            id="org-image-description"
            checked={enabled}
            disabled={!canEdit || update.isPending}
            onCheckedChange={setEnabled}
          />
        </FormFieldShell>
        {canEdit &&
          (enabled ? (
            <ImagePolicyEditor
              orgId={view.id}
              initial={initial}
              saving={update.isPending}
              onSave={save}
            />
          ) : (
            <Button disabled={!initial || update.isPending} onClick={() => save(null)}>
              {t('submit')}
            </Button>
          ))}
      </div>
    </SettingsSectionCard>
  )
}

function ImagePolicyEditor({
  orgId,
  initial,
  saving,
  onSave,
}: {
  orgId: string
  initial: OrgImageDescriptionPolicy | null
  saving: boolean
  onSave: (policy: OrgImageDescriptionPolicy) => void
}) {
  const t = useTranslations('orgAdmin.settings.imageDescription')
  const models = useModels()
  const catalog = useOrgCatalog(orgId)
  const loading = models.isLoading || catalog.isLoading
  const failed = models.isError || catalog.isError
  const allowed = new Set(
    catalog.data?.items
      .filter((item) => item.type === 'model' && item.enabled)
      .map((item) => item.id),
  )
  const choices = (failed ? [] : (models.data ?? [])).flatMap((model) => {
    const destination = allowed.has(model.id) ? imagePolicyDestination(model) : null
    return destination ? [{ model, destination }] : []
  })
  const [target, setTarget] = useState<ReturnType<typeof imagePolicyDestination>>(initial)
  const [values, setValues] = useState<Record<Limit, string>>({
    max_input_bytes: String(initial?.max_input_bytes ?? limits.max_input_bytes.initial),
    max_output_tokens: String(initial?.max_output_tokens ?? limits.max_output_tokens.initial),
    timeout_seconds: String(initial?.timeout_seconds ?? limits.timeout_seconds.initial),
    max_cost_usd: String(initial?.max_cost_usd ?? limits.max_cost_usd.initial),
  })
  // Require a new selection if the catalog changes the destination for a saved model ID.
  const choice = choices.find(
    ({ destination }) =>
      destination.model_id === target?.model_id &&
      destination.provider === target.provider &&
      destination.base_url === target.base_url,
  )
  const validLimits = limitKeys.every((key) => {
    const n = Number(values[key])
    const { min, max } = limits[key]
    return (
      n >= min &&
      n <= max &&
      (key === 'max_cost_usd' ? /^\d+(?:\.\d{1,8})?$/u.test(values[key]) : Number.isInteger(n))
    )
  })
  const dirty =
    !initial ||
    initial.base_url !== target?.base_url ||
    initial.provider !== target?.provider ||
    initial.model_id !== target?.model_id ||
    limitKeys.some((key) => Number(initial[key]) !== Number(values[key]))
  const bound =
    choice &&
    (choice.model.cost_per_input_token ?? 0) * (choice.model.context_window ?? 0) +
      (choice.model.cost_per_output_token ?? 0) * Number(values.max_output_tokens)
  const exceedsBudget =
    typeof bound === 'number' && Number(bound.toPrecision(15)) > Number(values.max_cost_usd)
  const canSave = choice && dirty && validLimits && !exceedsBudget && !loading && !failed && !saving

  return (
    <>
      <FormFieldShell id="org-image-model" label={t('model')} description={t('modelHint')}>
        <select
          id="org-image-model"
          aria-describedby="org-image-model-description"
          value={choice?.model.id ?? ''}
          disabled={loading || failed || saving}
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
          onChange={(event) =>
            setTarget(
              choices.find((item) => item.model.id === event.target.value)?.destination ?? null,
            )
          }
        >
          <option value="">{t('choose')}</option>
          {choices.map(({ model }) => (
            <option key={model.id} value={model.id}>
              {model.display_name}
            </option>
          ))}
        </select>
      </FormFieldShell>
      {failed ? (
        <p className="text-sm text-status-danger">{t('loadError')}</p>
      ) : !loading && !choices.length ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : target && !choice && !loading ? (
        <p className="text-sm text-status-warn">{t('changed')}</p>
      ) : null}
      {choice && (
        <dl className="moldy-muted-panel space-y-2 break-all p-3 text-sm">
          <div>
            <dt className="font-medium">{t('provider')}</dt>
            <dd>{choice.destination.provider}</dd>
          </div>
          <div>
            <dt className="font-medium">{t('destination')}</dt>
            <dd>{choice.destination.base_url}</dd>
          </div>
        </dl>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        {limitKeys.map((key) => (
          <FormFieldShell key={key} id={`org-image-${key}`} label={t(key)}>
            <Input
              id={`org-image-${key}`}
              type="number"
              min={limits[key].min}
              max={limits[key].max}
              step={limits[key].step}
              value={values[key]}
              disabled={saving}
              onChange={(event) => setValues({ ...values, [key]: event.target.value })}
            />
          </FormFieldShell>
        ))}
      </div>
      {!validLimits && <p className="text-sm text-status-danger">{t('invalidLimits')}</p>}
      {validLimits && exceedsBudget && (
        <p className="text-sm text-status-warn">{t('budgetTooLow')}</p>
      )}
      <Button
        disabled={!canSave}
        onClick={() => {
          if (!canSave) return
          onSave({
            ...choice.destination,
            max_input_bytes: Number(values.max_input_bytes),
            max_output_tokens: Number(values.max_output_tokens),
            timeout_seconds: Number(values.timeout_seconds),
            max_cost_usd: values.max_cost_usd,
          })
        }}
      >
        {t('submit')}
      </Button>
    </>
  )
}
