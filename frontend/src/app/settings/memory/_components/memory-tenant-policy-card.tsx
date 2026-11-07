'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { Building2Icon, Loader2Icon, SaveIcon } from 'lucide-react'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { ResourceListState } from '@/components/shared/resource-list-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Switch } from '@/components/ui/switch'
import { ApiError } from '@/lib/api/client'
import { useMemoryTenantPolicy, useUpdateMemoryTenantPolicy } from '@/lib/hooks/use-memory'
import type { MemoryApprovalMode, MemoryTenantPolicy, MemoryTenantPolicyValues } from '@/lib/types'
import { APPROVAL_MODES } from './approval-mode-chooser'
import { SelectField } from './memory-form-fields'

const SWITCHES = ['consolidation_enabled', 'channel_personal_memory', 'contacts_allowed'] as const
type SwitchKey = (typeof SWITCHES)[number]
type PolicyKey = keyof MemoryTenantPolicyValues
const KEYS: readonly PolicyKey[] = ['default_mode', 'allowed_modes', ...SWITCHES]

/** Allowed modes in the chooser's order, so order alone never reads as a change. */
function ordered(modes: MemoryApprovalMode[]): MemoryApprovalMode[] {
  return APPROVAL_MODES.filter((mode) => modes.includes(mode))
}

function sameValue(key: PolicyKey, a: MemoryTenantPolicyValues, b: MemoryTenantPolicyValues) {
  if (key === 'allowed_modes') {
    return ordered(a.allowed_modes).join(',') === ordered(b.allowed_modes).join(',')
  }
  return a[key] === b[key]
}

/**
 * The tenant memory policy (plan 15, 5.11, Q9): allowed and default approval modes and the
 * tenant switches. Company and personal tenants share the keys; only the defaults differ.
 */
export function MemoryTenantPolicyCard() {
  const t = useTranslations('appSettings.memory.tenantPolicy')
  const tMode = useTranslations('appSettings.memory.mode')
  const query = useMemoryTenantPolicy()
  const policy = query.data
  const kind = policy?.tenant_kind ?? 'company'

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <Building2Icon className="size-4" aria-hidden />
          {t(`title.${kind}`)}
        </span>
      }
      description={t('description')}
      actions={policy ? <Badge variant="outline">{tMode(`tenantKind.${kind}`)}</Badge> : null}
    >
      {policy ? (
        <TenantPolicyForm
          key={JSON.stringify([policy.values, policy.can_manage])}
          policy={policy}
        />
      ) : (
        <ResourceListState
          loading={query.isLoading}
          error={query.isError}
          onRetry={() => void query.refetch()}
          retryLabel={t('retry')}
          errorTitle={t('loadFailed')}
          skeleton={<p className="text-sm text-muted-foreground">{t('loading')}</p>}
          emptyTitle={t('loadFailed')}
          filteredEmptyTitle={t('loadFailed')}
        />
      )}
    </SettingsSectionCard>
  )
}

function TenantPolicyForm({ policy }: { policy: MemoryTenantPolicy }) {
  const t = useTranslations('appSettings.memory.tenantPolicy')
  const tMode = useTranslations('appSettings.memory.mode')
  const update = useUpdateMemoryTenantPolicy()
  const [draft, setDraft] = useState<MemoryTenantPolicyValues>(() => ({
    ...policy.values,
    allowed_modes: ordered(policy.values.allowed_modes),
  }))
  const shown = policy.can_manage ? draft : policy.values
  const dirty = KEYS.some((key) => !sameValue(key, draft, policy.values))
  const modeLabel = (mode: MemoryApprovalMode) => tMode(`options.${mode}.label`)

  function format(key: PolicyKey, values: MemoryTenantPolicyValues): string {
    if (key === 'default_mode') return modeLabel(values.default_mode)
    if (key === 'allowed_modes') return ordered(values.allowed_modes).map(modeLabel).join(', ')
    return values[key] ? t('on') : t('off')
  }

  /** "기본값: …" when the shown value differs from the tenant kind's default. */
  function defaultNote(key: PolicyKey): ReactNode {
    if (sameValue(key, shown, policy.defaults)) return null
    return (
      <span
        className="block text-xs text-muted-foreground"
        data-testid={`tenant-policy-default-${key}`}
      >
        {t('defaultValue', { value: format(key, policy.defaults) })}
      </span>
    )
  }

  function help(key: PolicyKey): ReactNode {
    return (
      <>
        {t(`fields.${key}.help`)}
        {defaultNote(key)}
      </>
    )
  }

  function toggleMode(mode: MemoryApprovalMode, checked: boolean) {
    setDraft((prev) => {
      const allowed = ordered(
        checked ? [...prev.allowed_modes, mode] : prev.allowed_modes.filter((m) => m !== mode),
      )
      if (allowed.length === 0) return prev
      // The default must stay among the allowed modes (MEMORY_POLICY_DEFAULT_NOT_ALLOWED).
      const defaultMode = allowed.includes(prev.default_mode) ? prev.default_mode : allowed[0]
      return { ...prev, allowed_modes: allowed, default_mode: defaultMode }
    })
  }

  async function handleSave() {
    try {
      await update.mutateAsync({ ...draft, allowed_modes: ordered(draft.allowed_modes) })
      toast.success(t('saved'))
    } catch (error) {
      if (error instanceof ApiError && error.code === 'MEMORY_POLICY_DEFAULT_NOT_ALLOWED') {
        toast.error(t('defaultNotAllowed'))
        return
      }
      if (error instanceof ApiError && error.status === 403) {
        toast.error(t('forbidden'))
        return
      }
      toast.error(error instanceof Error && error.message ? error.message : t('failed'))
    }
  }

  if (!policy.can_manage) {
    return (
      <div className="space-y-3" data-testid="tenant-policy-read-only">
        <dl className="grid gap-3 sm:grid-cols-2">
          {KEYS.map((key) => (
            <div
              key={key}
              className="space-y-1 rounded-lg border border-border/70 bg-background p-3"
              data-testid={`tenant-policy-value-${key}`}
            >
              <dt className="text-xs text-muted-foreground">{t(`fields.${key}.label`)}</dt>
              <dd className="text-sm font-medium text-foreground">{format(key, shown)}</dd>
              {defaultNote(key)}
            </div>
          ))}
        </dl>
        <p className="text-sm text-muted-foreground">{t('readOnly')}</p>
      </div>
    )
  }

  return (
    <div className="space-y-5" data-testid="tenant-policy-form">
      <SelectField
        id="tenant-policy-default-mode"
        label={t('fields.default_mode.label')}
        value={draft.default_mode}
        options={draft.allowed_modes.map((mode) => ({ value: mode, label: modeLabel(mode) }))}
        onChange={(value) =>
          setDraft((prev) => ({ ...prev, default_mode: value as MemoryApprovalMode }))
        }
      />
      {defaultNote('default_mode')}

      <fieldset className="space-y-2">
        <legend className="moldy-ui-label text-foreground">
          {t('fields.allowed_modes.label')}
        </legend>
        <p className="moldy-ui-caption text-muted-foreground">{help('allowed_modes')}</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {APPROVAL_MODES.map((mode) => {
            const checked = draft.allowed_modes.includes(mode)
            const last = checked && draft.allowed_modes.length === 1
            return (
              <label key={mode} className="flex items-center gap-2 text-sm text-foreground">
                <Checkbox
                  aria-label={modeLabel(mode)}
                  checked={checked}
                  disabled={last}
                  onCheckedChange={(value) => toggleMode(mode, Boolean(value))}
                  data-testid={`tenant-policy-allowed-${mode}`}
                />
                {modeLabel(mode)}
              </label>
            )
          })}
        </div>
      </fieldset>

      <div className="grid gap-3 md:grid-cols-3">
        {SWITCHES.map((key: SwitchKey) => (
          <FormFieldShell
            key={key}
            label={t(`fields.${key}.label`)}
            description={help(key)}
            layout="inline"
            className="rounded-lg border border-border/70 bg-background p-3"
          >
            <Switch
              aria-label={t(`fields.${key}.label`)}
              checked={draft[key]}
              onCheckedChange={(checked) => setDraft((prev) => ({ ...prev, [key]: checked }))}
              data-testid={`tenant-policy-${key}`}
            />
          </FormFieldShell>
        ))}
      </div>

      <div className="flex justify-end">
        <Button
          type="button"
          onClick={() => void handleSave()}
          disabled={!dirty || update.isPending}
          data-testid="tenant-policy-save"
        >
          {update.isPending ? (
            <Loader2Icon className="size-4 animate-spin" aria-hidden />
          ) : (
            <SaveIcon className="size-4" aria-hidden />
          )}
          {t('save')}
        </Button>
      </div>
    </div>
  )
}
