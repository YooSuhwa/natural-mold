'use client'

import { useState, useEffect, useCallback } from 'react'
import { ClockIcon, CalendarIcon, SettingsIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

type ScheduleType = 'minutes' | 'daily' | 'weekly' | 'monthly' | 'advanced'
type ConversationPolicy = 'schedule_thread' | 'new_per_run' | 'selected_conversation'

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
const WEEKDAY_CRON = [1, 2, 3, 4, 5, 6, 0] as const

const INTERVAL_OPTIONS = [5, 10, 20, 30, 60] as const

interface ScheduleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest }) => void
  trigger?: AgentTrigger | null
  isPending?: boolean
}

interface ScheduleFormProps {
  trigger?: AgentTrigger | null
  onSubmit: (data: TriggerCreateRequest) => void
  onCancel: () => void
  isPending?: boolean
  isEdit?: boolean
}

function parseTriggerToForm(trigger: AgentTrigger) {
  const config = trigger.schedule_config

  if (trigger.trigger_type === 'interval') {
    return {
      type: 'minutes' as ScheduleType,
      intervalMinutes: config.interval_minutes ?? 10,
      hour: '09',
      minute: '00',
      weekdays: new Set<number>(),
      monthDay: '1',
      cronExpression: '',
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
  }
}

const TYPE_ICONS: Record<ScheduleType, typeof ClockIcon> = {
  minutes: ClockIcon,
  daily: CalendarIcon,
  weekly: CalendarIcon,
  monthly: CalendarIcon,
  advanced: SettingsIcon,
}

export function ScheduleForm({
  trigger,
  onSubmit,
  onCancel,
  isPending,
  isEdit,
}: ScheduleFormProps) {
  const t = useTranslations('agent.schedule')

  const [form, setForm] = useState(getDefaultForm)
  const [scheduleName, setScheduleName] = useState('')
  const [inputMessage, setInputMessage] = useState('')
  const [conversationPolicy, setConversationPolicy] =
    useState<ConversationPolicy>('schedule_thread')
  const [targetConversationId, setTargetConversationId] = useState('')
  const [maxRuns, setMaxRuns] = useState('')
  const [endAt, setEndAt] = useState('')
  const [autoPauseAfterFailures, setAutoPauseAfterFailures] = useState('')

  const typeOptions: { value: ScheduleType; label: string; icon: typeof ClockIcon }[] = [
    { value: 'minutes', label: t('types.minutes'), icon: TYPE_ICONS.minutes },
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
    } else {
      setForm(getDefaultForm())
      setScheduleName('')
      setInputMessage('')
      setConversationPolicy('schedule_thread')
      setTargetConversationId('')
      setMaxRuns('')
      setEndAt('')
      setAutoPauseAfterFailures('')
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
    const parsedMaxRuns = maxRuns.trim() ? Number(maxRuns) : null
    const parsedFailures = autoPauseAfterFailures.trim() ? Number(autoPauseAfterFailures) : null

    return {
      ...req,
      conversation_policy: conversationPolicy,
      target_conversation_id:
        conversationPolicy === 'selected_conversation' && trimmedTarget ? trimmedTarget : null,
      max_runs: parsedMaxRuns && parsedMaxRuns > 0 ? parsedMaxRuns : null,
      end_at: endAt ? new Date(endAt).toISOString() : null,
      auto_pause_after_failures:
        parsedFailures && parsedFailures > 0 ? parsedFailures : null,
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

  const canSubmit = form.type !== 'advanced' || form.cronExpression.trim().length > 0

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t('labels.name')}</label>
        <Input
          value={scheduleName}
          onChange={(e) => setScheduleName(e.target.value)}
          placeholder={t('placeholders.name')}
        />
      </div>

      <div className="grid grid-cols-3 gap-2">
        {typeOptions.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            onClick={() => setForm((prev) => ({ ...prev, type: value }))}
            className={`flex flex-col items-center gap-1.5 rounded-lg border px-2 py-2.5 text-xs transition-colors ${
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

      {/* Type-specific settings — fixed height to prevent layout shift */}
      <div className="min-h-[120px] space-y-3">
        {form.type === 'minutes' && (
          <div className="flex items-center gap-2">
            <span className="text-sm">{t('labels.every')}</span>
            <Select
              value={String(form.intervalMinutes)}
              onValueChange={(v) => setForm((prev) => ({ ...prev, intervalMinutes: Number(v) }))}
            >
              <SelectTrigger className="w-20">
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

          {(form.type === 'daily' || form.type === 'weekly' || form.type === 'monthly') && (
            <>
              {form.type === 'weekly' && (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium">{t('labels.days')}</label>
                  <div className="flex gap-1">
                    {WEEKDAY_KEYS.map((key, i) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => toggleWeekday(WEEKDAY_CRON[i])}
                        className={`rounded-md px-2 py-1 text-xs transition-colors ${
                          form.weekdays.has(WEEKDAY_CRON[i])
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:bg-muted/80'
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
                    className="w-20"
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
                    className="w-16 text-center"
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
                    className="w-16 text-center"
                  />
                </div>
              </div>
            </>
          )}

          {form.type === 'advanced' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t('labels.cron')}</label>
              <Input
                value={form.cronExpression}
                onChange={(e) => setForm((prev) => ({ ...prev, cronExpression: e.target.value }))}
                placeholder={t('placeholders.cron')}
                className="font-mono text-sm"
              />
              <p className="text-xxs text-muted-foreground">{t('cronFormat')}</p>
            </div>
          )}
      </div>

      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t('labels.prompt')}</label>
        <Textarea
          value={inputMessage}
          onChange={(e) => setInputMessage(e.target.value)}
          placeholder={t('placeholders.prompt')}
          rows={2}
        />
        <p className="text-xxs text-muted-foreground">{t('promptDefault')}</p>
      </div>

      <div className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground">
        <div className="font-medium text-foreground">{t('resultStorage.title')}</div>
        <p className="mt-1">{t('resultStorage.description')}</p>
        <Select
          value={conversationPolicy}
          onValueChange={(value) => setConversationPolicy(value as ConversationPolicy)}
        >
          <SelectTrigger className="mt-3 h-8 bg-background">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="schedule_thread">{t('resultStorage.scheduleThread')}</SelectItem>
            <SelectItem value="new_per_run">{t('resultStorage.newPerRun')}</SelectItem>
            <SelectItem value="selected_conversation">
              {t('resultStorage.selectedConversation')}
            </SelectItem>
          </SelectContent>
        </Select>
        {conversationPolicy === 'selected_conversation' ? (
          <Input
            value={targetConversationId}
            onChange={(e) => setTargetConversationId(e.target.value)}
            placeholder={t('placeholders.conversationId')}
            className="mt-2 h-8 bg-background font-mono text-xs"
          />
        ) : null}
      </div>

      <div className="grid grid-cols-3 gap-2 rounded-lg border bg-background p-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t('labels.maxRuns')}</label>
          <Input
            type="number"
            min={1}
            value={maxRuns}
            onChange={(e) => setMaxRuns(e.target.value)}
            placeholder="∞"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t('labels.autoPause')}</label>
          <Input
            type="number"
            min={1}
            value={autoPauseAfterFailures}
            onChange={(e) => setAutoPauseAfterFailures(e.target.value)}
            placeholder="—"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-medium">{t('labels.endAt')}</label>
          <Input type="datetime-local" value={endAt} onChange={(e) => setEndAt(e.target.value)} />
        </div>
      </div>

      <div className="flex justify-end gap-2">
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
  isPending,
}: ScheduleDialogProps) {
  const t = useTranslations('agent.schedule')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        <ScheduleForm
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
