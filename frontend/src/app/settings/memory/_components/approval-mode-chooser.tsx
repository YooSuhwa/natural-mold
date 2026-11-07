'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckIcon, Loader2Icon, SaveIcon } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api/client'
import { useUpdateUserMemorySettings } from '@/lib/hooks/use-memory'
import type {
  MemoryApprovalAxes,
  MemoryApprovalAxis,
  MemoryApprovalMode,
  MemoryPolicy,
  UserMemorySettings,
  UserMemorySettingsUpdate,
} from '@/lib/types'
import { cn } from '@/lib/utils'
import { SelectField } from './memory-form-fields'

export const APPROVAL_MODES: readonly MemoryApprovalMode[] = [
  'off',
  'read_only',
  'approve_all',
  'partial_auto',
  'auto_notify',
  'custom',
]
const AXES: ReadonlyArray<keyof MemoryApprovalAxes> = ['records', 'profile', 'consolidation']
const AXIS_VALUES: readonly MemoryApprovalAxis[] = ['auto', 'approve', 'off']

interface ApprovalModeChooserProps {
  settings: UserMemorySettings
  policy: MemoryPolicy
  idPrefix: string
}

/**
 * Plan 15 (5.11) preset cards. A mode the tenant does not allow is disabled; the tenant
 * default is labelled. Same screen for company and personal tenants (Q9): `tenant_kind`
 * only names which default applies.
 */
export function ApprovalModeChooser(props: ApprovalModeChooserProps) {
  const { settings, policy } = props
  return (
    <ApprovalModeChooserInner
      key={JSON.stringify([settings.approval_mode, settings.approval_axes, policy.effective_mode])}
      {...props}
    />
  )
}

function ApprovalModeChooserInner({ settings, policy, idPrefix }: ApprovalModeChooserProps) {
  const t = useTranslations('appSettings.memory.mode')
  const update = useUpdateUserMemorySettings()
  const [mode, setMode] = useState<MemoryApprovalMode>(policy.effective_mode)
  const [axes, setAxes] = useState<MemoryApprovalAxes>(settings.approval_axes ?? policy.axes)
  const allowed = new Set(policy.allowed_modes)
  const tenant = t(`tenantKind.${policy.tenant_kind}`)

  const savedAxes = settings.approval_axes ?? policy.axes
  const dirty =
    settings.approval_mode !== mode ||
    (mode === 'custom' && JSON.stringify(axes) !== JSON.stringify(savedAxes))

  async function handleSave() {
    const payload: UserMemorySettingsUpdate =
      mode === 'custom' ? { approval_mode: 'custom', approval_axes: axes } : { approval_mode: mode }
    try {
      await update.mutateAsync(payload)
      toast.success(t('saved'))
    } catch (error) {
      if (error instanceof ApiError && error.code === 'MEMORY_MODE_NOT_ALLOWED') {
        toast.error(t('notAllowedError'))
        return
      }
      toast.error(error instanceof Error ? error.message : t('saveFailed'))
    }
  }

  return (
    <div className="space-y-4" data-testid={`${idPrefix}-approval-mode`}>
      {settings.approval_mode === null ? (
        <p className="text-sm text-muted-foreground">
          {t('defaultApplies', { tenant, mode: t(`options.${policy.default_mode}.label`) })}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {APPROVAL_MODES.map((option) => {
          const disabled = !allowed.has(option)
          const selected = mode === option
          return (
            <button
              key={option}
              type="button"
              aria-pressed={selected}
              disabled={disabled}
              onClick={() => setMode(option)}
              data-testid={`${idPrefix}-mode-${option}`}
              className={cn(
                'flex flex-col items-start gap-2 rounded-lg border bg-background p-3 text-left transition-colors',
                'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden',
                'disabled:cursor-not-allowed disabled:opacity-50',
                selected ? 'border-primary-strong' : 'border-border/70 hover:border-border',
              )}
            >
              <span className="flex w-full flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-foreground">
                  {t(`options.${option}.label`)}
                </span>
                {selected ? <CheckIcon className="size-4 text-primary-strong" aria-hidden /> : null}
                {policy.effective_mode === option ? (
                  <Badge variant="secondary">{t('current')}</Badge>
                ) : null}
                {policy.default_mode === option ? (
                  <Badge variant="outline">{t('default', { tenant })}</Badge>
                ) : null}
              </span>
              <span className="text-xs leading-5 text-muted-foreground">
                {disabled ? t('notAllowed') : t(`options.${option}.description`)}
              </span>
            </button>
          )
        })}
      </div>

      {mode === 'custom' ? (
        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">{t('axes.title')}</p>
          <div className="grid gap-4 md:grid-cols-3">
            {AXES.map((axis) => (
              <SelectField
                key={axis}
                id={`${idPrefix}-axis-${axis}`}
                label={t(`axes.${axis}`)}
                value={axes[axis]}
                options={AXIS_VALUES.map((value) => ({
                  value,
                  label: t(`axes.value.${value}`),
                }))}
                onChange={(value) =>
                  setAxes((prev) => ({ ...prev, [axis]: value as MemoryApprovalAxis }))
                }
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex justify-end">
        <Button
          type="button"
          onClick={handleSave}
          disabled={!dirty || !allowed.has(mode) || update.isPending}
          data-testid={`${idPrefix}-mode-save`}
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
