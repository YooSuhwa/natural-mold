'use client'

import { useState, useEffect, useCallback, useId, useMemo } from 'react'
import {
  CalendarIcon,
  CheckIcon,
  ChevronsUpDownIcon,
  ClockIcon,
  SearchIcon,
  SettingsIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useConversations } from '@/lib/hooks/use-conversations'
import { cn } from '@/lib/utils'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import type {
  AgentTrigger,
  Conversation,
  TriggerCreateRequest,
  TriggerUpdateRequest,
} from '@/lib/types'

type ScheduleType = 'minutes' | 'one_time' | 'daily' | 'weekly' | 'monthly' | 'advanced'
type ConversationPolicy = 'schedule_thread' | 'new_per_run' | 'selected_conversation'

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
const WEEKDAY_CRON = [1, 2, 3, 4, 5, 6, 0] as const

const INTERVAL_OPTIONS = [5, 10, 20, 30, 60] as const

interface ScheduleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest }) => void
  trigger?: AgentTrigger | null
  agentId?: string
  isPending?: boolean
}

interface ScheduleFormProps {
  agentId?: string
  trigger?: AgentTrigger | null
  onSubmit: (data: TriggerCreateRequest) => void
  onCancel: () => void
  isPending?: boolean
  isEdit?: boolean
}

function parseTriggerToForm(trigger: AgentTrigger) {
  const config = trigger.schedule_config ?? {}

  if (trigger.trigger_type === 'one_time') {
    return {
      type: 'one_time' as ScheduleType,
      intervalMinutes: 10,
      hour: '09',
      minute: '00',
      weekdays: new Set<number>(),
      monthDay: '1',
      cronExpression: '',
      scheduledAt: toDateTimeLocal(config.scheduled_at),
    }
  }

  if (trigger.trigger_type === 'interval') {
    return {
      type: 'minutes' as ScheduleType,
      intervalMinutes: config.interval_minutes ?? 10,
      hour: '09',
      minute: '00',
      weekdays: new Set<number>(),
      monthDay: '1',
      cronExpression: '',
      scheduledAt: defaultScheduledAt(),
    }
  }

  const cron = config.cron_expression ?? ''
  const parts = cron.split(' ')

  if (parts.length === 5) {
    const [cronMin, cronHour, cronDom, , cronDow] = parts
    const hour = cronHour.padStart(2, '0')
    const minute = cronMin.padStart(2, '0')

    if (cronDow !== '*') {
      const days = cronDow.split(',').map(Number)
      return {
        type: 'weekly' as ScheduleType,
        intervalMinutes: 10,
        hour,
        minute,
        weekdays: new Set(days),
        monthDay: '1',
        cronExpression: cron,
        scheduledAt: defaultScheduledAt(),
      }
    }

    if (cronDom !== '*') {
      return {
        type: 'monthly' as ScheduleType,
        intervalMinutes: 10,
        hour,
        minute,
        weekdays: new Set<number>(),
        monthDay: cronDom,
        cronExpression: cron,
        scheduledAt: defaultScheduledAt(),
      }
    }

    return {
      type: 'daily' as ScheduleType,
      intervalMinutes: 10,
      hour,
      minute,
      weekdays: new Set<number>(),
      monthDay: '1',
      cronExpression: cron,
      scheduledAt: defaultScheduledAt(),
    }
  }

  return {
    type: 'advanced' as ScheduleType,
    intervalMinutes: 10,
    hour: '09',
    minute: '00',
    weekdays: new Set<number>(),
    monthDay: '1',
    cronExpression: cron,
    scheduledAt: defaultScheduledAt(),
  }
}

function getDefaultForm() {
  return {
    type: 'minutes' as ScheduleType,
    intervalMinutes: 10,
    hour: '09',
    minute: '00',
    weekdays: new Set<number>(),
    monthDay: '1',
    cronExpression: '',
    scheduledAt: defaultScheduledAt(),
  }
}

const TYPE_ICONS: Record<ScheduleType, typeof ClockIcon> = {
  minutes: ClockIcon,
  one_time: CalendarIcon,
  daily: CalendarIcon,
  weekly: CalendarIcon,
  monthly: CalendarIcon,
  advanced: SettingsIcon,
}

function defaultScheduledAt() {
  const date = new Date()
  date.setHours(date.getHours() + 1, 0, 0, 0)
  return toDateTimeLocal(date.toISOString())
}

function toDateTimeLocal(value: string | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const offsetMs = date.getTimezoneOffset() * 60 * 1000
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16)
}

function conversationPolicyLabel(
  t: ReturnType<typeof useTranslations<'agent.schedule'>>,
  policy: ConversationPolicy,
) {
  if (policy === 'new_per_run') return t('resultStorage.newPerRun')
  if (policy === 'selected_conversation') return t('resultStorage.selectedConversation')
  return t('resultStorage.scheduleThread')
}

function formatConversationDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

interface ConversationComboboxProps {
  conversations: Conversation[]
  value: string
  onChange: (value: string) => void
  placeholder: string
  emptyLabel: string
  untitledLabel: string
  label: string
}

function ConversationCombobox({
  conversations,
  value,
  onChange,
  placeholder,
  emptyLabel,
  untitledLabel,
  label,
}: ConversationComboboxProps) {
  const t = useTranslations('agent.schedule')
  const listboxId = useId()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const selected = useMemo(
    () => conversations.find((conversation) => conversation.id === value),
    [conversations, value],
  )

  const filteredConversations = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return conversations
    return conversations.filter((conversation) =>
      (conversation.title || untitledLabel).toLocaleLowerCase().includes(query),
    )
  }, [conversations, search, untitledLabel])

  return (
    <div className="relative mt-2">
      <button
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label={label}
        onClick={() => setOpen((value) => !value)}
        className="flex h-10 w-full items-center justify-between rounded-md border bg-background px-3 text-left text-sm transition-colors hover:bg-muted"
      >
        <span className={cn('min-w-0 truncate', !selected && 'text-muted-foreground')}>
          {selected ? selected.title || untitledLabel : placeholder}
        </span>
        <ChevronsUpDownIcon className="ml-2 size-4 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-lg border bg-popover shadow-lg">
          <div className="flex items-center gap-2 border-b px-2.5 py-2">
            <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={placeholder}
              className="h-7 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div id={listboxId} role="listbox" className="max-h-44 overflow-y-auto p-1">
            {filteredConversations.length > 0 ? (
              filteredConversations.map((conversation) => {
                const title = conversation.title || untitledLabel
                const selectedItem = conversation.id === value
                return (
                  <button
                    key={conversation.id}
                    type="button"
                    role="option"
                    aria-selected={selectedItem}
                    onClick={() => {
                      onChange(conversation.id)
                      setOpen(false)
                      setSearch('')
                    }}
                    className={cn(
                      'flex w-full min-w-0 items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted',
                      selectedItem && 'bg-primary/10 text-foreground',
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{title}</span>
                      <span className="block truncate text-xxs text-muted-foreground">
                        {conversation.unread_count > 0
                          ? t('unreadCount', { count: conversation.unread_count })
                          : formatConversationDate(conversation.updated_at)}
                      </span>
                    </span>
                    <CheckIcon
                      className={cn('size-4 shrink-0', selectedItem ? 'opacity-100' : 'opacity-0')}
                    />
                  </button>
                )
              })
            ) : (
              <div className="px-2.5 py-3 text-sm text-muted-foreground">{emptyLabel}</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function ScheduleForm({
  agentId,
  trigger,
  onSubmit,
  onCancel,
  isPending,
  isEdit,
}: ScheduleFormProps) {
  const t = useTranslations('agent.schedule')
  const conversationsAgentId = agentId ?? trigger?.agent_id ?? ''
  const { data: conversations = [] } = useConversations(conversationsAgentId)

  const [form, setForm] = useState(getDefaultForm)
  const [scheduleName, setScheduleName] = useState('')
  const [inputMessage, setInputMessage] = useState('')
  const [conversationPolicy, setConversationPolicy] =
    useState<ConversationPolicy>('schedule_thread')
  const [targetConversationId, setTargetConversationId] = useState('')
  const [maxRuns, setMaxRuns] = useState('')
  const [endAt, setEndAt] = useState('')
  const [autoPauseAfterFailures, setAutoPauseAfterFailures] = useState('')
  const [maxRunsLimited, setMaxRunsLimited] = useState(false)
  const [autoPauseEnabled, setAutoPauseEnabled] = useState(false)

  const typeOptions: { value: ScheduleType; label: string; icon: typeof ClockIcon }[] = [
    { value: 'minutes', label: t('types.minutes'), icon: TYPE_ICONS.minutes },
    { value: 'one_time', label: t('types.oneTime'), icon: TYPE_ICONS.one_time },
    { value: 'daily', label: t('types.daily'), icon: TYPE_ICONS.daily },
    { value: 'weekly', label: t('types.weekly'), icon: TYPE_ICONS.weekly },
    { value: 'monthly', label: t('types.monthly'), icon: TYPE_ICONS.monthly },
    { value: 'advanced', label: t('types.advanced'), icon: TYPE_ICONS.advanced },
  ]

  /* eslint-disable react-hooks/set-state-in-effect -- reset form when trigger changes */
  useEffect(() => {
    if (trigger) {
      const parsed = parseTriggerToForm(trigger)
      setForm(parsed)
      setScheduleName(trigger.name)
      setInputMessage(trigger.input_message)
      setConversationPolicy(
        trigger.conversation_policy === 'new_per_run' ||
          trigger.conversation_policy === 'selected_conversation'
          ? trigger.conversation_policy
          : 'schedule_thread',
      )
      setTargetConversationId(trigger.target_conversation_id ?? '')
      setMaxRuns(trigger.max_runs ? String(trigger.max_runs) : '')
      setEndAt(trigger.end_at ? trigger.end_at.slice(0, 16) : '')
      setAutoPauseAfterFailures(
        trigger.auto_pause_after_failures ? String(trigger.auto_pause_after_failures) : '',
      )
      setMaxRunsLimited(!!trigger.max_runs)
      setAutoPauseEnabled(!!trigger.auto_pause_after_failures)
    } else {
      setForm(getDefaultForm())
      setScheduleName('')
      setInputMessage('')
      setConversationPolicy('schedule_thread')
      setTargetConversationId('')
      setMaxRuns('')
      setEndAt('')
      setAutoPauseAfterFailures('')
      setMaxRunsLimited(false)
      setAutoPauseEnabled(false)
    }
  }, [trigger])
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggleWeekday = useCallback((day: number) => {
    setForm((prev) => {
      const next = new Set(prev.weekdays)
      if (next.has(day)) next.delete(day)
      else next.add(day)
      return { ...prev, weekdays: next }
    })
  }, [])

  function withCommonOptions(req: TriggerCreateRequest): TriggerCreateRequest {
    const trimmedTarget = targetConversationId.trim()
    const parsedMaxRuns = maxRunsLimited && maxRuns.trim() ? Number(maxRuns) : null
    const parsedFailures =
      autoPauseEnabled && autoPauseAfterFailures.trim() ? Number(autoPauseAfterFailures) : null

    return {
      ...req,
      conversation_policy: conversationPolicy,
      target_conversation_id:
        conversationPolicy === 'selected_conversation' && trimmedTarget ? trimmedTarget : null,
      max_runs: parsedMaxRuns && parsedMaxRuns > 0 ? parsedMaxRuns : null,
      end_at: endAt ? new Date(endAt).toISOString() : null,
      auto_pause_after_failures: parsedFailures && parsedFailures > 0 ? parsedFailures : null,
    }
  }

  function buildRequest(): TriggerCreateRequest {
    switch (form.type) {
      case 'minutes':
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'interval',
          schedule_config: { interval_minutes: form.intervalMinutes },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
      case 'one_time':
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'one_time',
          schedule_config: { scheduled_at: new Date(form.scheduledAt).toISOString() },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
      case 'daily':
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * *` },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
      case 'weekly': {
        const days = Array.from(form.weekdays).sort().join(',') || '*'
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * ${days}` },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
      }
      case 'monthly':
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: {
            cron_expression: `${form.minute} ${form.hour} ${form.monthDay} * *`,
          },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
      case 'advanced':
        return withCommonOptions({
          name: scheduleName.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: form.cronExpression.trim() },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
          timezone: 'Asia/Seoul',
        })
    }
  }

  function handleSubmit() {
    onSubmit(buildRequest())
  }

  const canSubmit =
    (form.type !== 'advanced' || form.cronExpression.trim().length > 0) &&
    (form.type !== 'one_time' || form.scheduledAt.trim().length > 0) &&
    (conversationPolicy !== 'selected_conversation' || targetConversationId.trim().length > 0)

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="grid min-h-0 gap-5 overflow-y-auto px-6 py-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t('labels.name')}</label>
            <Input
              value={scheduleName}
              onChange={(e) => setScheduleName(e.target.value)}
              placeholder={t('placeholders.name')}
            />
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-2">
            {typeOptions.map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                type="button"
                onClick={() => setForm((prev) => ({ ...prev, type: value }))}
                className={`flex min-h-16 flex-col items-center justify-center gap-1.5 rounded-lg border px-2 py-2.5 text-xs transition-colors ${
                  form.type === value
                    ? 'border-primary bg-primary/10 text-foreground'
                    : 'border-border bg-background text-muted-foreground hover:bg-muted'
                }`}
              >
                <Icon className="size-4" />
                {label}
              </button>
            ))}
          </div>

          <div className="rounded-lg border bg-muted/30 p-3">
            {form.type === 'minutes' && (
              <div className="flex items-center gap-2">
                <span className="text-sm">{t('labels.every')}</span>
                <Select
                  value={String(form.intervalMinutes)}
                  onValueChange={(v) =>
                    setForm((prev) => ({ ...prev, intervalMinutes: Number(v) }))
                  }
                >
                  <SelectTrigger className="w-20 bg-background">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {INTERVAL_OPTIONS.map((m) => (
                      <SelectItem key={m} value={String(m)}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span className="text-sm">{t('labels.minutes')}</span>
              </div>
            )}

            {form.type === 'one_time' && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">{t('labels.scheduledAt')}</label>
                <Input
                  type="datetime-local"
                  aria-label={t('labels.scheduledAt')}
                  value={form.scheduledAt}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      scheduledAt: e.target.value,
                    }))
                  }
                  className="bg-background"
                />
              </div>
            )}

            {(form.type === 'daily' || form.type === 'weekly' || form.type === 'monthly') && (
              <div className="space-y-3">
                {form.type === 'weekly' && (
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">{t('labels.days')}</label>
                    <div className="flex flex-wrap gap-1">
                      {WEEKDAY_KEYS.map((key, i) => (
                        <button
                          key={key}
                          type="button"
                          onClick={() => toggleWeekday(WEEKDAY_CRON[i])}
                          className={`rounded-md px-2 py-1 text-xs transition-colors ${
                            form.weekdays.has(WEEKDAY_CRON[i])
                              ? 'bg-primary text-primary-foreground'
                              : 'bg-background text-muted-foreground ring-1 ring-border hover:bg-muted'
                          }`}
                        >
                          {t(`weekdays.${key}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {form.type === 'monthly' && (
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">{t('labels.dayOfMonth')}</label>
                    <Input
                      type="number"
                      min={1}
                      max={31}
                      value={form.monthDay}
                      onChange={(e) => setForm((prev) => ({ ...prev, monthDay: e.target.value }))}
                      className="w-20 bg-background"
                    />
                  </div>
                )}

                <div className="space-y-1.5">
                  <label className="text-xs font-medium">{t('labels.time')}</label>
                  <div className="flex items-center gap-1">
                    <Input
                      type="number"
                      min={0}
                      max={23}
                      value={form.hour}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          hour: e.target.value.padStart(2, '0'),
                        }))
                      }
                      className="w-16 bg-background text-center"
                    />
                    <span className="text-sm font-medium">:</span>
                    <Input
                      type="number"
                      min={0}
                      max={59}
                      value={form.minute}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          minute: e.target.value.padStart(2, '0'),
                        }))
                      }
                      className="w-16 bg-background text-center"
                    />
                  </div>
                </div>
              </div>
            )}

            {form.type === 'advanced' && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium">{t('labels.cron')}</label>
                <Input
                  value={form.cronExpression}
                  onChange={(e) => setForm((prev) => ({ ...prev, cronExpression: e.target.value }))}
                  placeholder={t('placeholders.cron')}
                  className="bg-background font-mono text-sm"
                />
                <p className="text-xxs text-muted-foreground">{t('cronFormat')}</p>
              </div>
            )}
          </div>
        </section>

        <section className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t('labels.prompt')}</label>
            <Textarea
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              placeholder={t('placeholders.prompt')}
              rows={5}
            />
            <p className="text-xxs text-muted-foreground">{t('promptDefault')}</p>
          </div>

          <div className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground">
            <div className="font-medium text-foreground">{t('resultStorage.title')}</div>
            <p className="mt-1">{t('resultStorage.description')}</p>
            <div className="mt-3 grid gap-1.5">
              {(['schedule_thread', 'new_per_run', 'selected_conversation'] as const).map(
                (policy) => (
                  <button
                    key={policy}
                    type="button"
                    aria-pressed={conversationPolicy === policy}
                    onClick={() => setConversationPolicy(policy)}
                    className={`rounded-md border px-2.5 py-2 text-left text-xs transition-colors ${
                      conversationPolicy === policy
                        ? 'border-primary bg-primary/10 text-foreground'
                        : 'border-border bg-background text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    {conversationPolicyLabel(t, policy)}
                  </button>
                ),
              )}
            </div>
            {conversationPolicy === 'selected_conversation' ? (
              conversations.length > 0 ? (
                <ConversationCombobox
                  conversations={conversations}
                  value={targetConversationId}
                  onChange={setTargetConversationId}
                  placeholder={t('placeholders.conversation')}
                  emptyLabel={t('resultStorage.noConversations')}
                  untitledLabel={t('resultStorage.untitledConversation')}
                  label={t('labels.conversation')}
                />
              ) : (
                <div className="mt-2 rounded-md border bg-background px-2.5 py-2 text-xs text-muted-foreground">
                  {t('resultStorage.noConversations')}
                </div>
              )
            ) : null}
          </div>

          <div className="grid gap-2 rounded-lg border bg-background p-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs font-medium">{t('labels.maxRuns')}</label>
                <Button
                  type="button"
                  variant={maxRunsLimited ? 'outline' : 'secondary'}
                  size="xs"
                  aria-label={
                    maxRunsLimited ? t('limits.maxRunsUnlimited') : t('limits.maxRunsLimited')
                  }
                  onClick={() => setMaxRunsLimited((value) => !value)}
                >
                  {maxRunsLimited ? t('limits.limited') : t('limits.unlimited')}
                </Button>
              </div>
              {maxRunsLimited ? (
                <Input
                  aria-label={t('labels.maxRunsValue')}
                  type="number"
                  min={1}
                  value={maxRuns}
                  onChange={(e) => setMaxRuns(e.target.value)}
                  placeholder="10"
                />
              ) : (
                <div className="flex h-8 items-center rounded-lg border bg-muted/30 px-2.5 text-sm text-muted-foreground">
                  {t('limits.unlimited')}
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs font-medium">{t('labels.autoPause')}</label>
                <Button
                  type="button"
                  variant={autoPauseEnabled ? 'outline' : 'secondary'}
                  size="xs"
                  aria-label={
                    autoPauseEnabled ? t('limits.autoPauseDisabled') : t('limits.autoPauseEnabled')
                  }
                  onClick={() => setAutoPauseEnabled((value) => !value)}
                >
                  {autoPauseEnabled ? t('limits.enabled') : t('limits.disabled')}
                </Button>
              </div>
              {autoPauseEnabled ? (
                <Input
                  aria-label={t('labels.autoPauseValue')}
                  type="number"
                  min={1}
                  value={autoPauseAfterFailures}
                  onChange={(e) => setAutoPauseAfterFailures(e.target.value)}
                  placeholder="3"
                />
              ) : (
                <div className="flex h-8 items-center rounded-lg border bg-muted/30 px-2.5 text-sm text-muted-foreground">
                  {t('limits.disabled')}
                </div>
              )}
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-xs font-medium">{t('labels.endAt')}</label>
              <Input
                type="datetime-local"
                value={endAt}
                onChange={(e) => setEndAt(e.target.value)}
              />
            </div>
          </div>
        </section>
      </div>

      <div className="flex shrink-0 justify-end gap-2 border-t bg-background/95 px-6 py-4">
        <Button variant="outline" onClick={onCancel}>
          {t('cancel')}
        </Button>
        <Button onClick={handleSubmit} disabled={!canSubmit || isPending}>
          {isEdit ? t('update') : t('create')}
        </Button>
      </div>
    </div>
  )
}

export function ScheduleDialog({
  open,
  onOpenChange,
  onSubmit,
  trigger,
  agentId,
  isPending,
}: ScheduleDialogProps) {
  const t = useTranslations('agent.schedule')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="fixed">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body className="flex min-h-0 flex-1 space-y-0 overflow-hidden p-0">
        <ScheduleForm
          agentId={agentId}
          trigger={trigger}
          isEdit={!!trigger}
          isPending={isPending}
          onCancel={() => onOpenChange(false)}
          onSubmit={(req) => {
            if (trigger) onSubmit({ triggerId: trigger.id, data: req })
            else onSubmit(req)
          }}
        />
      </DialogShell.Body>
    </DialogShell>
  )
}
