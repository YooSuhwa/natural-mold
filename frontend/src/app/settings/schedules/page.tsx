'use client'

import { useDeferredValue, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircleIcon,
  CalendarClockIcon,
  ExternalLinkIcon,
  HistoryIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { useFormatter, useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ResourceListState } from '@/components/shared/resource-list-state'
import { SearchFilterBar } from '@/components/shared/search-filter-bar'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { ScheduleDialog } from '@/features/schedules/components/schedule-dialog'
import { ScheduleRunList } from '@/features/schedules/components/schedule-run-list'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  useAllTriggers,
  useDeleteTriggerGlobal,
  useRunTriggerNow,
  useTriggerRuns,
  useUpdateTriggerGlobal,
} from '@/lib/hooks/use-triggers'
import {
  formatScheduleCenterLabel,
  getScheduleStatusVariant,
} from '@/features/schedules/lib/cron-labels'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'
import { SettingsShell } from '../_components/settings-shell'

const ALL_STATUSES = 'all'
const ALL_AGENTS = 'all'

export default function SchedulesPage() {
  const t = useTranslations('scheduleCenter')
  const { data: triggers, isLoading } = useAllTriggers()
  const updateTrigger = useUpdateTriggerGlobal()
  const deleteTrigger = useDeleteTriggerGlobal()
  const runNow = useRunTriggerNow()
  const format = useFormatter()

  const [searchQuery, setSearchQuery] = useState('')
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [statusFilter, setStatusFilter] = useState<string>(ALL_STATUSES)
  const [agentFilter, setAgentFilter] = useState<string>(ALL_AGENTS)
  const [editingTrigger, setEditingTrigger] = useState<AgentTrigger | null>(null)
  const [historyTrigger, setHistoryTrigger] = useState<AgentTrigger | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AgentTrigger | null>(null)
  const { data: runs, isLoading: runsLoading } = useTriggerRuns(historyTrigger?.id ?? null)
  const scheduleLabels = useMemo(
    () => ({
      interval: (minutes: number) => t('format.interval', { minutes }),
      oneTime: t('format.oneTime'),
      cron: t('format.cron'),
    }),
    [t],
  )

  const totalUnread = useMemo(
    () =>
      (triggers ?? []).reduce(
        (sum, trigger) => sum + (trigger.schedule_conversation_unread_count ?? 0),
        0,
      ),
    [triggers],
  )
  const activeCount = useMemo(
    () => (triggers ?? []).filter((trigger) => trigger.status === 'active').length,
    [triggers],
  )
  const agentOptions = useMemo(() => {
    const options = new Map<string, string>()
    for (const trigger of triggers ?? []) {
      options.set(trigger.agent_id, trigger.agent_name ?? t('agentFallback'))
    }
    return Array.from(options, ([id, name]) => ({ id, name })).sort((a, b) =>
      a.name.localeCompare(b.name, 'ko'),
    )
  }, [t, triggers])
  const filteredTriggers = useMemo(() => {
    const normalizedQuery = deferredSearchQuery.trim().toLocaleLowerCase()
    return (triggers ?? []).filter((trigger) => {
      if (statusFilter !== ALL_STATUSES && trigger.status !== statusFilter) return false
      if (agentFilter !== ALL_AGENTS && trigger.agent_id !== agentFilter) return false
      if (!normalizedQuery) return true
      const haystack = [
        trigger.name,
        trigger.input_message,
        trigger.agent_name,
        trigger.schedule_conversation_title,
        formatScheduleCenterLabel(trigger, scheduleLabels),
        t(`status.${trigger.status}`),
      ]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase()
      return haystack.includes(normalizedQuery)
    })
  }, [agentFilter, deferredSearchQuery, scheduleLabels, statusFilter, t, triggers])
  const hasActiveFilters =
    searchQuery.trim().length > 0 || statusFilter !== ALL_STATUSES || agentFilter !== ALL_AGENTS
  const triggerCount = triggers?.length ?? 0
  const showScheduleState = isLoading || triggerCount === 0 || filteredTriggers.length === 0
  const selectedAgentNameById = useMemo(
    () => new Map(agentOptions.map((agent) => [agent.id, agent.name])),
    [agentOptions],
  )

  function formatDate(value: string | null) {
    if (!value) return t('none')
    return format.dateTime(new Date(value), {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Seoul',
    })
  }

  function handleToggle(trigger: AgentTrigger) {
    updateTrigger.mutate({
      triggerId: trigger.id,
      data: { status: trigger.status === 'active' ? 'paused' : 'active' },
    })
  }

  function handleEdit(
    payload: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest },
  ) {
    if (!editingTrigger) return
    const data = 'data' in payload ? payload.data : payload
    updateTrigger.mutate(
      { triggerId: editingTrigger.id, data },
      { onSuccess: () => setEditingTrigger(null) },
    )
  }

  return (
    <SettingsShell wide>
      <div className="flex w-full flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CalendarClockIcon className="size-4" />
              {t('eyebrow')}
            </div>
            <h1 className="mt-1 text-2xl font-semibold">{t('title')}</h1>
            <p className="mt-1 text-sm text-muted-foreground">{t('description')}</p>
          </div>
          <div className="flex gap-2">
            <Badge variant="secondary">{t('badges.active', { count: activeCount })}</Badge>
            {totalUnread > 0 ? <Badge>{t('badges.unread', { count: totalUnread })}</Badge> : null}
          </div>
        </div>

        <SettingsSectionCard
          title={t('listTitle')}
          actions={
            <div className="text-xs text-muted-foreground">
              {filteredTriggers.length} / {triggerCount}
            </div>
          }
        >
          <div className="space-y-4">
            <SearchFilterBar
              value={searchQuery}
              onValueChange={setSearchQuery}
              searchLabel={t('searchPlaceholder')}
              placeholder={t('searchPlaceholder')}
              filters={
                <>
                  <Select
                    value={statusFilter}
                    onValueChange={(value) => setStatusFilter(value ?? ALL_STATUSES)}
                  >
                    <SelectTrigger
                      className="w-full bg-background sm:w-40"
                      aria-label={t('filters.status')}
                    >
                      <SelectValue>
                        {(selected) =>
                          selected === ALL_STATUSES
                            ? t('filters.allStatus')
                            : t(`status.${selected as AgentTrigger['status']}`)
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ALL_STATUSES}>{t('filters.allStatus')}</SelectItem>
                      <SelectItem value="active">{t('status.active')}</SelectItem>
                      <SelectItem value="paused">{t('status.paused')}</SelectItem>
                      <SelectItem value="completed">{t('status.completed')}</SelectItem>
                      <SelectItem value="error">{t('status.error')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select
                    value={agentFilter}
                    onValueChange={(value) => setAgentFilter(value ?? ALL_AGENTS)}
                  >
                    <SelectTrigger
                      className="w-full bg-background sm:w-48"
                      aria-label={t('filters.agent')}
                    >
                      <SelectValue>
                        {(selected) =>
                          !selected || selected === ALL_AGENTS
                            ? t('filters.allAgents')
                            : (selectedAgentNameById.get(selected) ?? t('agentFallback'))
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ALL_AGENTS}>{t('filters.allAgents')}</SelectItem>
                      {agentOptions.map((agent) => (
                        <SelectItem key={agent.id} value={agent.id}>
                          {agent.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </>
              }
              resetLabel={hasActiveFilters ? t('reset') : undefined}
              onReset={
                hasActiveFilters
                  ? () => {
                      setSearchQuery('')
                      setStatusFilter(ALL_STATUSES)
                      setAgentFilter(ALL_AGENTS)
                    }
                  : undefined
              }
            />

            {showScheduleState ? (
              <ResourceListState
                loading={isLoading}
                isFiltered={triggerCount > 0 && filteredTriggers.length === 0}
                skeleton={
                  <div className="space-y-2">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <Skeleton key={i} className="h-16 w-full" />
                    ))}
                  </div>
                }
                emptyTitle={t('empty.description')}
                filteredEmptyTitle={t('empty.filtered')}
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border/70">
                <table className="w-full text-sm">
                  <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                    <tr>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.schedule')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.agent')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.frequency')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.status')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.nextRun')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.lastRun')}</th>
                      <th className="px-4 py-3 text-left font-medium">{t('columns.result')}</th>
                      <th className="px-4 py-3 text-right font-medium">{t('columns.actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTriggers.map((trigger) => (
                      <tr key={trigger.id} className="border-b last:border-0">
                        <td className="px-4 py-3 align-top">
                          <div className="font-medium">{trigger.name}</div>
                          <div className="mt-1 max-w-xs truncate text-xs text-muted-foreground">
                            {trigger.input_message}
                          </div>
                          {trigger.last_error ? (
                            <div className="mt-1 flex items-center gap-1 text-xs text-destructive">
                              <AlertCircleIcon className="size-3" />
                              {trigger.last_error}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 align-top">
                          <Link
                            href={`/agents/${trigger.agent_id}/settings`}
                            className="text-primary-strong hover:underline"
                          >
                            {trigger.agent_name ?? t('agentFallback')}
                          </Link>
                        </td>
                        <td className="px-4 py-3 align-top text-xs">
                          <span
                            className={
                              trigger.trigger_type === 'one_time' ? undefined : 'font-mono'
                            }
                          >
                            {formatScheduleCenterLabel(trigger, scheduleLabels)}
                          </span>
                          {trigger.trigger_type === 'one_time' ? (
                            <div className="mt-1 text-muted-foreground">
                              {formatDate(trigger.schedule_config.scheduled_at ?? null)}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 align-top">
                          <Badge variant={getScheduleStatusVariant(trigger.status)}>
                            {t(`status.${trigger.status}`)}
                          </Badge>
                        </td>
                        <td className="px-4 py-3 align-top text-muted-foreground">
                          {formatDate(trigger.next_run_at)}
                        </td>
                        <td className="px-4 py-3 align-top text-muted-foreground">
                          {formatDate(trigger.last_run_at)}
                        </td>
                        <td className="px-4 py-3 align-top">
                          {trigger.schedule_conversation_id ? (
                            <Link
                              href={`/agents/${trigger.agent_id}/conversations/${trigger.schedule_conversation_id}`}
                              className="inline-flex items-center gap-1 text-primary-strong hover:underline"
                            >
                              {trigger.schedule_conversation_title ??
                                t('resultConversationFallback')}
                              <ExternalLinkIcon className="size-3" />
                              {(trigger.schedule_conversation_unread_count ?? 0) > 0 ? (
                                <Badge className="ml-1" variant="secondary">
                                  {trigger.schedule_conversation_unread_count}
                                </Badge>
                              ) : null}
                            </Link>
                          ) : (
                            <span className="text-muted-foreground">{t('notYet')}</span>
                          )}
                        </td>
                        <td className="px-4 py-3 align-top">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t('actions.runNow')}
                              onClick={() => runNow.mutate(trigger.id)}
                              disabled={runNow.isPending}
                            >
                              <RefreshCwIcon className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={
                                trigger.status === 'active'
                                  ? t('actions.pause')
                                  : t('actions.resume')
                              }
                              onClick={() => handleToggle(trigger)}
                              disabled={updateTrigger.isPending || trigger.status === 'completed'}
                            >
                              {trigger.status === 'active' ? (
                                <PauseIcon className="size-4" />
                              ) : (
                                <PlayIcon className="size-4" />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t('actions.edit')}
                              onClick={() => setEditingTrigger(trigger)}
                            >
                              <PencilIcon className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t('actions.history')}
                              onClick={() => setHistoryTrigger(trigger)}
                            >
                              <HistoryIcon className="size-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={t('actions.delete')}
                              onClick={() => setDeleteTarget(trigger)}
                            >
                              <Trash2Icon className="size-4 text-muted-foreground" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </SettingsSectionCard>

        <ScheduleDialog
          open={!!editingTrigger}
          onOpenChange={(open) => !open && setEditingTrigger(null)}
          trigger={editingTrigger}
          agentId={editingTrigger?.agent_id}
          isPending={updateTrigger.isPending}
          onSubmit={handleEdit}
        />

        <DialogShell
          open={!!historyTrigger}
          onOpenChange={(open) => !open && setHistoryTrigger(null)}
          size="lg"
          height="auto"
        >
          <DialogShell.Header title={t('history.title')} description={historyTrigger?.name} />
          <DialogShell.Body>
            <ScheduleRunList
              runs={runs}
              loading={runsLoading}
              formatDate={formatDate}
              labels={{
                runNow: t('history.runNow'),
                scheduled: t('history.scheduled'),
                openConversation: t('history.openConversation'),
                empty: t('history.empty'),
              }}
            />
          </DialogShell.Body>
        </DialogShell>

        <DeleteConfirmDialog
          open={!!deleteTarget}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          title={t('delete.title')}
          description={t('delete.description')}
          cancelLabel={t('delete.cancel')}
          confirmLabel={t('delete.confirm')}
          isPending={deleteTrigger.isPending}
          onConfirm={() => {
            if (!deleteTarget) return
            deleteTrigger.mutate(deleteTarget.id, { onSuccess: () => setDeleteTarget(null) })
          }}
        />
      </div>
    </SettingsShell>
  )
}
