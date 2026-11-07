'use client'

import { useTranslations } from 'next-intl'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import type { MemoryScopeQuery, MemoryScopeSelection } from '@/lib/types'

export interface MemoryAgentOption {
  id: string
  name: string
}

export function ToggleField({
  id,
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  id: string
  label: string
  description: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <FormFieldShell
      label={label}
      description={description}
      layout="inline"
      className="rounded-lg border border-border/70 bg-background p-3"
    >
      <Checkbox
        id={id}
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(value) => onChange(Boolean(value))}
      />
    </FormFieldShell>
  )
}

export function SelectField({
  id,
  label,
  description,
  value,
  options,
  disabled,
  onChange,
}: {
  id: string
  label: string
  description?: string
  value: string
  options: Array<{ value: string; label: string; disabled?: boolean }>
  disabled?: boolean
  onChange: (value: string) => void
}) {
  const selected = options.find((option) => option.value === value)
  return (
    <FormFieldShell id={id} label={label} description={description}>
      <Select value={value} onValueChange={(next) => next && onChange(next)} disabled={disabled}>
        <SelectTrigger id={id} className="w-full" data-testid={`${id}-trigger`}>
          <span className="truncate">{selected?.label ?? value}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              disabled={option.disabled}
              data-testid={`${id}-option-${option.value}`}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FormFieldShell>
  )
}

const SCOPE_SELECTIONS: readonly MemoryScopeSelection[] = ['all', 'user', 'agent']

/** Scope (all / personal / one agent) for export and scoped delete (plan 15, 5.7). */
export function MemoryScopeFields({
  idPrefix,
  value,
  agents,
  onChange,
}: {
  idPrefix: string
  value: MemoryScopeQuery
  agents: MemoryAgentOption[]
  onChange: (value: MemoryScopeQuery) => void
}) {
  const t = useTranslations('appSettings.memory.scopeSelect')
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <SelectField
        id={`${idPrefix}-scope`}
        label={t('label')}
        value={value.scope}
        options={SCOPE_SELECTIONS.map((scope) => ({ value: scope, label: t(scope) }))}
        onChange={(scope) =>
          onChange({
            scope: scope as MemoryScopeSelection,
            agentId: scope === 'agent' ? (value.agentId ?? agents[0]?.id ?? null) : null,
          })
        }
      />
      {value.scope === 'agent' ? (
        <SelectField
          id={`${idPrefix}-agent`}
          label={t('agent_label')}
          value={value.agentId ?? ''}
          options={agents.map((agent) => ({ value: agent.id, label: agent.name }))}
          disabled={agents.length === 0}
          onChange={(agentId) => onChange({ scope: 'agent', agentId })}
        />
      ) : null}
    </div>
  )
}
