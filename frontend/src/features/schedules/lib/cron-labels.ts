import type { AgentTrigger } from '@/lib/types'

interface ScheduleSummaryLabels {
  everyNMin: (minutes: number) => string
  atTimeDays: (time: string, days: string) => string
  atTimeDay: (time: string, day: string) => string
  atTimeEveryDay: (time: string) => string
  weekdays: Record<string, string>
}

interface ScheduleCenterLabels {
  interval: (minutes: number) => string
  oneTime: string
  cron: string
}

export function formatTriggerSummary(trigger: AgentTrigger, labels: ScheduleSummaryLabels): string {
  const config = trigger.schedule_config ?? {}
  if (trigger.trigger_type === 'one_time') {
    return config.scheduled_at ?? trigger.name
  }

  if (trigger.trigger_type === 'interval') {
    return labels.everyNMin(config.interval_minutes ?? 10)
  }

  const cron = config.cron_expression ?? ''
  const parts = cron.split(' ')
  if (parts.length !== 5) return cron

  const [min, hour, dom, , dow] = parts
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`

  if (dow !== '*') {
    const days = dow
      .split(',')
      .map((day) => labels.weekdays[day] ?? day)
      .join(', ')
    return labels.atTimeDays(time, days)
  }

  if (dom !== '*') {
    return labels.atTimeDay(time, dom)
  }

  return labels.atTimeEveryDay(time)
}

export function formatScheduleCenterLabel(
  trigger: AgentTrigger,
  labels: ScheduleCenterLabels,
): string {
  if (trigger.trigger_type === 'interval') {
    return labels.interval(trigger.schedule_config.interval_minutes ?? 10)
  }
  if (trigger.trigger_type === 'one_time') {
    return labels.oneTime
  }
  return trigger.schedule_config.cron_expression ?? labels.cron
}

export function getScheduleStatusVariant(
  status: AgentTrigger['status'],
): 'default' | 'secondary' | 'destructive' {
  if (status === 'active') return 'default'
  if (status === 'error') return 'destructive'
  return 'secondary'
}
