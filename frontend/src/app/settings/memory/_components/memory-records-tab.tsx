'use client'

import { useMemo, useState, type FormEvent } from 'react'
import { useTranslations } from 'next-intl'
import { BrainIcon, Loader2Icon, PlusIcon } from 'lucide-react'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { ResourceListState } from '@/components/shared/resource-list-state'
import { SearchFilterBar } from '@/components/shared/search-filter-bar'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useCreateMemory, useMemories } from '@/lib/hooks/use-memory'
import type { MemoryScope, MemoryScopeFilter, MemoryStatusFilter } from '@/lib/types'
import { SelectField, type MemoryAgentOption } from './memory-form-fields'
import { MemoryRecordItem } from './memory-record-item'

const NONE_AGENT = '__none__'
const STATUS_FILTERS: readonly MemoryStatusFilter[] = ['active', 'revoked', 'review', 'all']

export function MemoryRecordsTab({
  agents,
  agentsLoading,
}: {
  agents: MemoryAgentOption[]
  agentsLoading: boolean
}) {
  const t = useTranslations('appSettings.memory')
  const agentNameById = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.name])),
    [agents],
  )
  const [scopeFilter, setScopeFilter] = useState<MemoryScopeFilter>('all')
  const [statusFilter, setStatusFilter] = useState<MemoryStatusFilter>('active')
  const [search, setSearch] = useState('')
  const { data: memories, isLoading } = useMemories({
    scope: scopeFilter,
    status: statusFilter,
    q: search.trim() || null,
  })
  const hasFilters = scopeFilter !== 'all' || statusFilter !== 'active' || search.trim().length > 0

  const emptyState = (
    <ResourceListState
      loading={isLoading}
      isFiltered={hasFilters}
      skeleton={<p className="text-sm text-muted-foreground">{t('list.loading')}</p>}
      emptyTitle={t('list.emptyTitle')}
      emptyDescription={t('list.emptyDescription')}
      filteredEmptyTitle={t('list.emptyTitle')}
      filteredEmptyDescription={t('list.emptyDescription')}
    />
  )

  return (
    <div className="space-y-4">
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
              <>
                <Select
                  value={statusFilter}
                  onValueChange={(value) => value && setStatusFilter(value as MemoryStatusFilter)}
                >
                  <SelectTrigger
                    className="w-full bg-background sm:w-40"
                    aria-label={t('statusFilter.label')}
                    data-testid="memory-status-filter"
                  >
                    <span>{t(`statusFilter.${statusFilter}`)}</span>
                  </SelectTrigger>
                  <SelectContent>
                    {STATUS_FILTERS.map((status) => (
                      <SelectItem
                        key={status}
                        value={status}
                        data-testid={`memory-status-filter-${status}`}
                      >
                        {t(`statusFilter.${status}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={scopeFilter}
                  onValueChange={(value) => value && setScopeFilter(value as MemoryScopeFilter)}
                >
                  <SelectTrigger
                    className="w-full bg-background sm:w-40"
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
              </>
            }
          />

          {!isLoading && memories && memories.length > 0 ? (
            <div className="space-y-3" data-testid="memory-list">
              {memories.map((memory) => (
                <MemoryRecordItem
                  key={`${memory.id}:${memory.updated_at}:${memory.status}`}
                  memory={memory}
                  agentName={memory.agent_id ? agentNameById.get(memory.agent_id) : null}
                />
              ))}
            </div>
          ) : (
            emptyState
          )}
        </div>
      </SettingsSectionCard>
    </div>
  )
}

function CreateMemoryCard({
  agents,
  agentsLoading,
}: {
  agents: MemoryAgentOption[]
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
