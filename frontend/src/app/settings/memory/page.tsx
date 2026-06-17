'use client'

import { useMemo, useState, type FormEvent } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import {
  BrainIcon,
  CheckIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  SaveIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { ResourceListState } from '@/components/shared/resource-list-state'
import { SearchFilterBar } from '@/components/shared/search-filter-bar'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useAgents } from '@/lib/hooks/use-agents'
import {
  useCreateMemory,
  useDeleteMemory,
  useMemories,
  useUpdateMemory,
  useUpdateUserMemorySettings,
  useUserMemorySettings,
} from '@/lib/hooks/use-memory'
import type {
  MemoryAllowedScopes,
  MemoryRecord,
  MemoryScope,
  MemoryScopeFilter,
  MemoryWritePolicy,
  TriggerMemoryWritePolicy,
  UserMemorySettings,
} from '@/lib/types'
import { SettingsShell } from '../_components/settings-shell'

const NONE_AGENT = '__none__'

function asAgentList(
  agents: Array<{ id: string; name: string; description?: string | null }> | undefined,
) {
  return agents ?? []
}

export default function MemorySettingsPage() {
  const t = useTranslations('appSettings.memory')
  const { data: settings, isLoading: settingsLoading } = useUserMemorySettings()

  return (
    <SettingsShell>
      <div className="space-y-4" data-testid="memory-settings-page">
        <section className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        </section>

        {settingsLoading || !settings ? (
          <SettingsSectionCard title={t('title')} description={t('description')}>
            <p className="text-sm text-muted-foreground">{t('loading')}</p>
          </SettingsSectionCard>
        ) : (
          <MemorySettingsInner key={JSON.stringify(settings)} settings={settings} />
        )}
      </div>
    </SettingsShell>
  )
}

function MemorySettingsInner({ settings }: { settings: UserMemorySettings }) {
  const t = useTranslations('appSettings.memory')
  const { data: agentsData, isLoading: agentsLoading } = useAgents()
  const agents = asAgentList(agentsData)
  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.name])),
    [agents],
  )

  const [scopeFilter, setScopeFilter] = useState<MemoryScopeFilter>('all')
  const [search, setSearch] = useState('')
  const { data: memories, isLoading: memoriesLoading } = useMemories({
    scope: scopeFilter,
    q: search.trim() || null,
  })
  const hasMemoryFilters = scopeFilter !== 'all' || search.trim().length > 0

  return (
    <div className="space-y-4">
      <PolicyCard settings={settings} />
      <CreateMemoryCard agents={agents} agentsLoading={agentsLoading} />
      <SettingsSectionCard
        title={
          <span className="flex items-center gap-2">
            <BrainIcon className="size-4" aria-hidden />
            {t('list.title')}
          </span>
        }
        description={t('list.description')}
      >
        <div className="space-y-4">
          <SearchFilterBar
            value={search}
            onValueChange={setSearch}
            searchLabel={t('list.searchLabel')}
            placeholder={t('list.searchPlaceholder')}
            filters={
              <Select
                value={scopeFilter}
                onValueChange={(value) => value && setScopeFilter(value as MemoryScopeFilter)}
              >
                <SelectTrigger
                  className="w-full bg-background sm:w-[150px]"
                  aria-label={t('list.scopeFilter')}
                >
                  <span>{t(`scopeFilter.${scopeFilter}`)}</span>
                </SelectTrigger>
                <SelectContent>
                  {(['all', 'user', 'agent'] as const).map((scope) => (
                    <SelectItem key={scope} value={scope}>
                      {t(`scopeFilter.${scope}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />

          {memoriesLoading ? (
            <ResourceListState
              loading
              skeleton={<p className="text-sm text-muted-foreground">{t('list.loading')}</p>}
              emptyTitle={t('list.emptyTitle')}
              emptyDescription={t('list.emptyDescription')}
              filteredEmptyTitle={t('list.emptyTitle')}
              filteredEmptyDescription={t('list.emptyDescription')}
            />
          ) : memories && memories.length > 0 ? (
            <div className="space-y-3" data-testid="memory-list">
              {memories.map((memory) => (
                <MemoryRecordItem
                  key={`${memory.id}:${memory.updated_at}`}
                  memory={memory}
                  agentName={memory.agent_id ? agentNameById.get(memory.agent_id) : null}
                />
              ))}
            </div>
          ) : (
            <ResourceListState
              isFiltered={hasMemoryFilters}
              skeleton={<p className="text-sm text-muted-foreground">{t('list.loading')}</p>}
              emptyTitle={t('list.emptyTitle')}
              emptyDescription={t('list.emptyDescription')}
              filteredEmptyTitle={t('list.emptyTitle')}
              filteredEmptyDescription={t('list.emptyDescription')}
            />
          )}
        </div>
      </SettingsSectionCard>
    </div>
  )
}

function PolicyCard({ settings }: { settings: UserMemorySettings }) {
  const t = useTranslations('appSettings.memory')
  const update = useUpdateUserMemorySettings()
  const [draft, setDraft] = useState<UserMemorySettings>(settings)

  const dirty = JSON.stringify(draft) !== JSON.stringify(settings)

  async function handleSave() {
    try {
      await update.mutateAsync(draft)
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
              setDraft((prev) => ({
                ...prev,
                memory_write_policy: value as MemoryWritePolicy,
              }))
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
              setDraft((prev) => ({
                ...prev,
                allowed_scopes: value as MemoryAllowedScopes,
              }))
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

function CreateMemoryCard({
  agents,
  agentsLoading,
}: {
  agents: Array<{ id: string; name: string; description?: string | null }>
  agentsLoading: boolean
}) {
  const t = useTranslations('appSettings.memory')
  const create = useCreateMemory()
  const [scope, setScope] = useState<MemoryScope>('user')
  const [agentId, setAgentId] = useState<string>(agents[0]?.id ?? NONE_AGENT)
  const [content, setContent] = useState('')
  const [reason, setReason] = useState('')

  const selectedAgentId = scope === 'agent' && agentId !== NONE_AGENT ? agentId : null
  const canSubmit = content.trim().length > 0 && (scope === 'user' || selectedAgentId !== null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    try {
      await create.mutateAsync({
        scope,
        content: content.trim(),
        reason: reason.trim() || null,
        agent_id: selectedAgentId,
      })
      setContent('')
      setReason('')
      toast.success(t('toast.created'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.createFailed'))
    }
  }

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <PlusIcon className="size-4" aria-hidden />
          {t('create.title')}
        </span>
      }
      description={t('create.description')}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <SelectField
            id="memory-create-scope"
            label={t('create.scope')}
            value={scope}
            options={(['user', 'agent'] as const).map((value) => ({
              value,
              label: t(`scope.${value}`),
            }))}
            onChange={(value) => setScope(value as MemoryScope)}
          />
          <FormFieldShell
            id="memory-create-agent"
            label={t('create.agent')}
            description={scope === 'agent' ? t('create.agentHelp') : t('create.userHelp')}
          >
            <Select
              value={agentId}
              onValueChange={(value) => value && setAgentId(value)}
              disabled={scope !== 'agent' || agentsLoading || agents.length === 0}
            >
              <SelectTrigger id="memory-create-agent" className="w-full">
                <span className="truncate">
                  {scope !== 'agent'
                    ? t('create.agentDisabled')
                    : (agents.find((agent) => agent.id === agentId)?.name ?? t('create.agent'))}
                </span>
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormFieldShell>
        </div>

        <FormFieldShell id="memory-content" label={t('create.content')}>
          <Textarea
            id="memory-content"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder={t('create.contentPlaceholder')}
            className="min-h-24"
            data-testid="memory-content-input"
          />
        </FormFieldShell>

        <FormFieldShell id="memory-reason" label={t('create.reason')}>
          <Input
            id="memory-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={t('create.reasonPlaceholder')}
          />
        </FormFieldShell>

        <div className="flex justify-end">
          <Button
            type="submit"
            disabled={!canSubmit || create.isPending}
            data-testid="memory-create-submit"
          >
            {create.isPending ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <PlusIcon className="size-4" aria-hidden />
            )}
            {t('create.submit')}
          </Button>
        </div>
      </form>
    </SettingsSectionCard>
  )
}

function MemoryRecordItem({
  memory,
  agentName,
}: {
  memory: MemoryRecord
  agentName: string | null | undefined
}) {
  const t = useTranslations('appSettings.memory')
  const format = useFormatter()
  const update = useUpdateMemory()
  const remove = useDeleteMemory()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [content, setContent] = useState(memory.content)
  const [reason, setReason] = useState(memory.reason ?? '')

  const updatedAt = format.dateTime(new Date(memory.updated_at), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'Asia/Seoul',
  })

  async function handleSave() {
    try {
      await update.mutateAsync({
        id: memory.id,
        data: {
          content: content.trim(),
          reason: reason.trim() || null,
        },
      })
      setEditing(false)
      toast.success(t('toast.updated'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.updateFailed'))
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(memory.id)
      toast.success(t('toast.deleted'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('toast.deleteFailed'))
    }
  }

  return (
    <article
      className="rounded-lg border border-border/70 bg-background p-3"
      data-testid={`memory-item-${memory.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{t(`scope.${memory.scope}`)}</Badge>
            {agentName ? <Badge variant="outline">{agentName}</Badge> : null}
            <span className="text-xs text-muted-foreground">
              {t('list.updatedAt', { date: updatedAt })}
            </span>
          </div>
          {editing ? (
            <div className="space-y-3">
              <Textarea
                value={content}
                onChange={(event) => setContent(event.target.value)}
                className="min-h-20"
                aria-label={t('list.editContent')}
                data-testid="memory-edit-content"
              />
              <Input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t('list.reasonPlaceholder')}
                aria-label={t('list.editReason')}
              />
            </div>
          ) : (
            <div className="space-y-1">
              <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
                {memory.content}
              </p>
              {memory.reason ? (
                <p className="text-xs text-muted-foreground">{memory.reason}</p>
              ) : null}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {editing ? (
            <>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setEditing(false)}
                aria-label={t('list.cancel')}
                data-testid="memory-cancel-edit"
              >
                <XIcon className="size-4" aria-hidden />
              </Button>
              <Button
                type="button"
                size="icon-sm"
                onClick={handleSave}
                disabled={update.isPending || content.trim().length === 0}
                aria-label={t('list.save')}
                data-testid="memory-save-edit"
              >
                {update.isPending ? (
                  <Loader2Icon className="size-4 animate-spin" aria-hidden />
                ) : (
                  <CheckIcon className="size-4" aria-hidden />
                )}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setEditing(true)}
                aria-label={t('list.edit')}
                data-testid="memory-edit-button"
              >
                <PencilIcon className="size-4" aria-hidden />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setConfirmDelete(true)}
                aria-label={t('list.delete')}
                data-testid="memory-delete-button"
              >
                <Trash2Icon className="size-4" aria-hidden />
              </Button>
            </>
          )}
        </div>
      </div>
      {confirmDelete ? (
        <div className="mt-3" data-testid="memory-delete-confirm">
          <DeleteConfirmInline
            entity={t('list.memoryEntity')}
            pending={remove.isPending}
            onCancel={() => setConfirmDelete(false)}
            onConfirm={handleDelete}
          />
        </div>
      ) : null}
    </article>
  )
}

function ToggleField({
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

function SelectField({
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
  options: Array<{ value: string; label: string }>
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
