'use client'

import { useState } from 'react'
import { BrainIcon, Loader2Icon, SaveIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import { useAgentMemorySettings, useUpdateAgentMemorySettings } from '@/lib/hooks/use-memory'
import type {
  AgentMemoryPolicyOverride,
  AgentMemoryScopesOverride,
  AgentMemorySettings,
  AgentTriggerMemoryPolicyOverride,
} from '@/lib/types'

interface AgentMemorySettingsSectionProps {
  agentId: string
}

export function AgentMemorySettingsSection({ agentId }: AgentMemorySettingsSectionProps) {
  const t = useTranslations('agent.settings.memory')
  const { data, isLoading } = useAgentMemorySettings(agentId)

  if (isLoading || !data) {
    return (
      <section className="rounded-lg border px-4 py-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" aria-hidden />
          {t('loading')}
        </div>
      </section>
    )
  }

  return <AgentMemorySettingsForm key={JSON.stringify(data)} agentId={agentId} settings={data} />
}

function AgentMemorySettingsForm({
  agentId,
  settings,
}: {
  agentId: string
  settings: AgentMemorySettings
}) {
  const t = useTranslations('agent.settings.memory')
  const update = useUpdateAgentMemorySettings(agentId)
  const [draft, setDraft] = useState<AgentMemorySettings>(settings)
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings)

  async function handleSave() {
    try {
      await update.mutateAsync(draft)
      toast.success(t('toast.saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.saveFailed'))
    }
  }

  return (
    <section className="rounded-lg border px-4 py-3" data-testid="agent-memory-settings">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BrainIcon className="size-4 text-muted-foreground" aria-hidden />
            {t('title')}
          </div>
          <div className="mt-0.5 text-xs leading-5 text-muted-foreground">
            {t('description')}
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <PanelSelect
          label={t('writePolicy')}
          testId="agent-memory-write-policy"
          value={draft.memory_policy_override}
          options={(['inherit', 'off', 'ask', 'auto'] as const).map((value) => ({
            value,
            label: t(`writePolicyOptions.${value}`),
          }))}
          onChange={(value) =>
            setDraft((prev) => ({
              ...prev,
              memory_policy_override: value as AgentMemoryPolicyOverride,
            }))
          }
        />
        <PanelSelect
          label={t('scopePolicy')}
          testId="agent-memory-scope-policy"
          value={draft.memory_scopes_override}
          options={(['inherit', 'agent_only', 'user_and_agent'] as const).map((value) => ({
            value,
            label: t(`scopeOptions.${value}`),
          }))}
          onChange={(value) =>
            setDraft((prev) => ({
              ...prev,
              memory_scopes_override: value as AgentMemoryScopesOverride,
            }))
          }
        />
        <PanelSelect
          label={t('triggerPolicy')}
          testId="agent-memory-trigger-policy"
          value={draft.trigger_memory_policy_override}
          options={(['inherit', 'off', 'auto'] as const).map((value) => ({
            value,
            label: t(`triggerOptions.${value}`),
          }))}
          onChange={(value) =>
            setDraft((prev) => ({
              ...prev,
              trigger_memory_policy_override: value as AgentTriggerMemoryPolicyOverride,
            }))
          }
        />
      </div>

      <div className="mt-3 flex justify-end">
        <Button
          type="button"
          size="sm"
          onClick={handleSave}
          disabled={!dirty || update.isPending}
          data-testid="agent-memory-settings-save"
        >
          {update.isPending ? (
            <Loader2Icon className="size-4 animate-spin" aria-hidden />
          ) : (
            <SaveIcon className="size-4" aria-hidden />
          )}
          {t('save')}
        </Button>
      </div>
    </section>
  )
}

function PanelSelect({
  label,
  testId,
  value,
  options,
  onChange,
}: {
  label: string
  testId: string
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
}) {
  const selected = options.find((option) => option.value === value)
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <Select value={value} onValueChange={(next) => next && onChange(next)}>
        <SelectTrigger className="w-full" data-testid={`${testId}-trigger`}>
          <span className="truncate">{selected?.label ?? value}</span>
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              data-testid={`${testId}-option-${option.value}`}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
