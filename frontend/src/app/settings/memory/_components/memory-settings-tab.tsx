'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  BrainIcon,
  DownloadIcon,
  ListChecksIcon,
  Loader2Icon,
  SaveIcon,
  Trash2Icon,
} from 'lucide-react'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { useExportMemories, useUpdateUserMemorySettings } from '@/lib/hooks/use-memory'
import type {
  MemoryAllowedScopes,
  MemoryExportFormat,
  MemoryPolicy,
  MemoryScopeQuery,
  MemoryWritePolicy,
  TriggerMemoryWritePolicy,
  UserMemorySettings,
  UserMemorySettingsUpdate,
} from '@/lib/types'
import { ApprovalModeChooser } from './approval-mode-chooser'
import { MemoryBulkDeleteFlow } from './memory-bulk-delete'
import {
  MemoryScopeFields,
  SelectField,
  ToggleField,
  type MemoryAgentOption,
} from './memory-form-fields'

export function MemorySettingsTab({
  settings,
  policy,
  agents,
}: {
  settings: UserMemorySettings
  policy: MemoryPolicy | undefined
  agents: MemoryAgentOption[]
}) {
  const t = useTranslations('appSettings.memory')
  return (
    <div className="space-y-4">
      <SettingsSectionCard
        title={
          <span className="flex items-center gap-2" id="memory-approval-mode-heading">
            <ListChecksIcon className="size-4" aria-hidden />
            {t('mode.title')}
          </span>
        }
        description={t('mode.description')}
      >
        {policy ? (
          <ApprovalModeChooser settings={settings} policy={policy} idPrefix="settings" />
        ) : (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        )}
      </SettingsSectionCard>
      <ConsolidationCard settings={settings} policy={policy} />
      <PolicyCard
        key={JSON.stringify(LEGACY_FIELDS.map((field) => settings[field]))}
        settings={settings}
      />
      <ExportCard agents={agents} />
      <BulkDeleteCard agents={agents} />
    </div>
  )
}

const LEGACY_FIELDS = [
  'memory_enabled',
  'memory_read_enabled',
  'memory_write_policy',
  'allowed_scopes',
  'trigger_memory_write_policy',
] as const

type LegacyDraft = Pick<UserMemorySettings, (typeof LEGACY_FIELDS)[number]>

function PolicyCard({ settings }: { settings: UserMemorySettings }) {
  const t = useTranslations('appSettings.memory')
  const update = useUpdateUserMemorySettings()
  const [draft, setDraft] = useState<LegacyDraft>(() => ({
    memory_enabled: settings.memory_enabled,
    memory_read_enabled: settings.memory_read_enabled,
    memory_write_policy: settings.memory_write_policy,
    allowed_scopes: settings.allowed_scopes,
    trigger_memory_write_policy: settings.trigger_memory_write_policy,
  }))

  // Only the changed fields: the approval mode keeps these in step on its own save.
  const changes: UserMemorySettingsUpdate = {}
  for (const field of LEGACY_FIELDS) {
    if (draft[field] !== settings[field]) Object.assign(changes, { [field]: draft[field] })
  }
  const dirty = Object.keys(changes).length > 0

  async function handleSave() {
    try {
      await update.mutateAsync(changes)
      toast.success(t('toast.settingsSaved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.settingsFailed'))
    }
  }

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <BrainIcon className="size-4" aria-hidden />
          {t('policy.title')}
        </span>
      }
      description={t('policy.description')}
      actions={
        <Badge variant={draft.memory_enabled ? 'default' : 'outline'}>
          {draft.memory_enabled ? t('policy.enabledBadge') : t('policy.disabledBadge')}
        </Badge>
      }
    >
      <div className="space-y-5">
        <div className="grid gap-3 md:grid-cols-2">
          <ToggleField
            id="memory-enabled"
            label={t('policy.memoryEnabled')}
            description={t('policy.memoryEnabledHelp')}
            checked={draft.memory_enabled}
            onChange={(checked) => setDraft((prev) => ({ ...prev, memory_enabled: checked }))}
          />
          <ToggleField
            id="memory-read-enabled"
            label={t('policy.memoryReadEnabled')}
            description={t('policy.memoryReadEnabledHelp')}
            checked={draft.memory_read_enabled}
            disabled={!draft.memory_enabled}
            onChange={(checked) => setDraft((prev) => ({ ...prev, memory_read_enabled: checked }))}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <SelectField
            id="memory-write-policy"
            label={t('policy.writePolicy')}
            description={t('policy.writePolicyHelp')}
            value={draft.memory_write_policy}
            disabled={!draft.memory_enabled}
            options={(['off', 'ask', 'auto'] as const).map((value) => ({
              value,
              label: t(`writePolicy.${value}`),
            }))}
            onChange={(value) =>
              setDraft((prev) => ({ ...prev, memory_write_policy: value as MemoryWritePolicy }))
            }
          />
          <SelectField
            id="memory-allowed-scopes"
            label={t('policy.allowedScopes')}
            description={t('policy.allowedScopesHelp')}
            value={draft.allowed_scopes}
            disabled={!draft.memory_enabled}
            options={(['user', 'agent', 'both'] as const).map((value) => ({
              value,
              label: t(`allowedScopes.${value}`),
            }))}
            onChange={(value) =>
              setDraft((prev) => ({ ...prev, allowed_scopes: value as MemoryAllowedScopes }))
            }
          />
          <SelectField
            id="memory-trigger-policy"
            label={t('policy.triggerPolicy')}
            description={t('policy.triggerPolicyHelp')}
            value={draft.trigger_memory_write_policy}
            disabled={!draft.memory_enabled}
            options={(['off', 'auto'] as const).map((value) => ({
              value,
              label: t(`triggerPolicy.${value}`),
            }))}
            onChange={(value) =>
              setDraft((prev) => ({
                ...prev,
                trigger_memory_write_policy: value as TriggerMemoryWritePolicy,
              }))
            }
          />
        </div>

        <div className="flex justify-end">
          <Button
            type="button"
            onClick={handleSave}
            disabled={!dirty || update.isPending}
            data-testid="memory-settings-save"
          >
            {update.isPending ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <SaveIcon className="size-4" aria-hidden />
            )}
            {t('policy.save')}
          </Button>
        </div>
      </div>
    </SettingsSectionCard>
  )
}

function ConsolidationCard({
  settings,
  policy,
}: {
  settings: UserMemorySettings
  policy: MemoryPolicy | undefined
}) {
  const t = useTranslations('appSettings.memory.consolidation')
  const update = useUpdateUserMemorySettings()
  const tenantOff = policy ? !policy.tenant_consolidation_enabled : false

  async function handleChange(checked: boolean) {
    try {
      await update.mutateAsync({ consolidation_enabled: checked })
      toast.success(t('saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('failed'))
    }
  }

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <FormFieldShell
        label={t('label')}
        description={tenantOff ? t('tenantOff') : t('help')}
        layout="inline"
        className="rounded-lg border border-border/70 bg-background p-3"
      >
        <Switch
          aria-label={t('label')}
          checked={settings.consolidation_enabled}
          disabled={update.isPending}
          onCheckedChange={(checked) => void handleChange(checked)}
          data-testid="memory-consolidation-switch"
        />
      </FormFieldShell>
    </SettingsSectionCard>
  )
}

function ExportCard({ agents }: { agents: MemoryAgentOption[] }) {
  const t = useTranslations('appSettings.memory.export')
  const exportMemories = useExportMemories()
  const [format, setFormat] = useState<MemoryExportFormat>('json')
  const [query, setQuery] = useState<MemoryScopeQuery>({ scope: 'all', agentId: null })
  const ready = query.scope !== 'agent' || Boolean(query.agentId)

  function handleExport() {
    exportMemories.mutate(
      { format, query },
      {
        onSuccess: () => toast.success(t('done')),
        onError: (error) => toast.error(error instanceof Error ? error.message : t('failed')),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <DownloadIcon className="size-4" aria-hidden />
          {t('title')}
        </span>
      }
      description={t('description')}
    >
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-3">
          <SelectField
            id="memory-export-format"
            label={t('format')}
            value={format}
            options={(['json', 'markdown'] as const).map((value) => ({
              value,
              label: t(value),
            }))}
            onChange={(value) => setFormat(value as MemoryExportFormat)}
          />
          <div className="md:col-span-2">
            <MemoryScopeFields
              idPrefix="memory-export"
              value={query}
              agents={agents}
              onChange={setQuery}
            />
          </div>
        </div>
        <div className="flex justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={handleExport}
            disabled={!ready || exportMemories.isPending}
            data-testid="memory-export-submit"
          >
            {exportMemories.isPending ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <DownloadIcon className="size-4" aria-hidden />
            )}
            {t('submit')}
          </Button>
        </div>
      </div>
    </SettingsSectionCard>
  )
}

function BulkDeleteCard({ agents }: { agents: MemoryAgentOption[] }) {
  const t = useTranslations('appSettings.memory.bulkDelete')
  const [query, setQuery] = useState<MemoryScopeQuery>({ scope: 'all', agentId: null })
  const [active, setActive] = useState(false)
  // Each check remounts the flow, so the count is fetched fresh every time.
  const [attempt, setAttempt] = useState(0)
  const ready = query.scope !== 'agent' || Boolean(query.agentId)

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <Trash2Icon className="size-4" aria-hidden />
          {t('title')}
        </span>
      }
      description={t('description')}
    >
      <div className="space-y-4">
        <MemoryScopeFields
          idPrefix="memory-bulk"
          value={query}
          agents={agents}
          onChange={(next) => {
            setActive(false)
            setQuery(next)
          }}
        />
        <MemoryBulkDeleteFlow
          key={attempt}
          query={query}
          active={active}
          onClose={() => setActive(false)}
        />
        <div className="flex justify-end">
          <Button
            type="button"
            variant="destructive"
            onClick={() => {
              setAttempt((n) => n + 1)
              setActive(true)
            }}
            disabled={!ready}
            data-testid="memory-bulk-check"
          >
            <Trash2Icon className="size-4" aria-hidden />
            {t('check')}
          </Button>
        </div>
      </div>
    </SettingsSectionCard>
  )
}
