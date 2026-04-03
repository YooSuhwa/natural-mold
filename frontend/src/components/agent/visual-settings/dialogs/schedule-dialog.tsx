'use client'

import { useState, useEffect, useCallback } from 'react'
import { ClockIcon, CalendarIcon, SettingsIcon } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
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

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
const WEEKDAY_CRON = [1, 2, 3, 4, 5, 6, 0] as const

const INTERVAL_OPTIONS = [5, 10, 20, 30, 60] as const

interface ScheduleDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest }) => void
  trigger?: AgentTrigger | null
  isPending?: boolean
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

const TYPE_OPTIONS: { value: ScheduleType; label: string; icon: typeof ClockIcon }[] = [
  { value: 'minutes', label: 'Every few minutes', icon: ClockIcon },
  { value: 'daily', label: 'Daily', icon: CalendarIcon },
  { value: 'weekly', label: 'Weekly', icon: CalendarIcon },
  { value: 'monthly', label: 'Monthly', icon: CalendarIcon },
  { value: 'advanced', label: 'Advanced', icon: SettingsIcon },
]

export function ScheduleDialog({
  open,
  onOpenChange,
  onSubmit,
  trigger,
  isPending,
}: ScheduleDialogProps) {
  const isEdit = !!trigger

  const [form, setForm] = useState(getDefaultForm)
  const [inputMessage, setInputMessage] = useState('')

  /* eslint-disable react-hooks/set-state-in-effect -- reset form when dialog opens/trigger changes */
  useEffect(() => {
    if (!open) return
    if (trigger) {
      const parsed = parseTriggerToForm(trigger)
      setForm(parsed)
      setInputMessage(trigger.input_message)
    } else {
      setForm(getDefaultForm())
      setInputMessage('')
    }
  }, [open, trigger])
  /* eslint-enable react-hooks/set-state-in-effect */

  const toggleWeekday = useCallback((day: number) => {
    setForm((prev) => {
      const next = new Set(prev.weekdays)
      if (next.has(day)) next.delete(day)
      else next.add(day)
      return { ...prev, weekdays: next }
    })
  }, [])

  function buildRequest(): TriggerCreateRequest {
    switch (form.type) {
      case 'minutes':
        return {
          trigger_type: 'interval',
          schedule_config: { interval_minutes: form.intervalMinutes },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
        }
      case 'daily':
        return {
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * *` },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
        }
      case 'weekly': {
        const days = Array.from(form.weekdays).sort().join(',') || '*'
        return {
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * ${days}` },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
        }
      }
      case 'monthly':
        return {
          trigger_type: 'cron',
          schedule_config: {
            cron_expression: `${form.minute} ${form.hour} ${form.monthDay} * *`,
          },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
        }
      case 'advanced':
        return {
          trigger_type: 'cron',
          schedule_config: { cron_expression: form.cronExpression.trim() },
          input_message: inputMessage.trim() || 'Cron trigger fired.',
        }
    }
  }

  function handleSubmit() {
    const req = buildRequest()
    if (isEdit && trigger) {
      onSubmit({ triggerId: trigger.id, data: req })
    } else {
      onSubmit(req)
    }
  }

  const canSubmit = form.type !== 'advanced' || form.cronExpression.trim().length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Schedule</DialogTitle>
          <DialogDescription>Choose when to run your agent. Times are in UTC.</DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-3 gap-2">
          {TYPE_OPTIONS.map(({ value, label, icon: Icon }) => (
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
              <span className="text-sm">Every</span>
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
              <span className="text-sm">minutes</span>
            </div>
          )}

          {(form.type === 'daily' || form.type === 'weekly' || form.type === 'monthly') && (
            <>
              {form.type === 'weekly' && (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium">Days</label>
                  <div className="flex gap-1">
                    {WEEKDAYS.map((day, i) => (
                      <button
                        key={day}
                        type="button"
                        onClick={() => toggleWeekday(WEEKDAY_CRON[i])}
                        className={`rounded-md px-2 py-1 text-xs transition-colors ${
                          form.weekdays.has(WEEKDAY_CRON[i])
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:bg-muted/80'
                        }`}
                      >
                        {day}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {form.type === 'monthly' && (
                <div className="space-y-1.5">
                  <label className="text-xs font-medium">Day of month</label>
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
                <label className="text-xs font-medium">Time (UTC)</label>
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
              <label className="text-xs font-medium">Cron expression</label>
              <Input
                value={form.cronExpression}
                onChange={(e) => setForm((prev) => ({ ...prev, cronExpression: e.target.value }))}
                placeholder="0 9 * * 1-5"
                className="font-mono text-sm"
              />
              <p className="text-xxs text-muted-foreground">
                Format: minute hour day-of-month month day-of-week
              </p>
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-medium">Prompt (optional)</label>
          <Textarea
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            placeholder="Message sent to the agent on each run..."
            rows={2}
          />
          <p className="text-xxs text-muted-foreground">
            Defaults to &quot;Cron trigger fired.&quot;
          </p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit || isPending}>
            {isEdit ? 'Update schedule' : 'Create schedule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
