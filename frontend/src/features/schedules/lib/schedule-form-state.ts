import type { AgentTrigger, TriggerCreateRequest } from '@/lib/types'

export type ScheduleType = 'minutes' | 'one_time' | 'daily' | 'weekly' | 'monthly' | 'advanced'
export type ConversationPolicy = 'schedule_thread' | 'new_per_run' | 'selected_conversation'

export interface ScheduleFormState {
  type: ScheduleType
  intervalMinutes: number
  hour: string
  minute: string
  weekdays: Set<number>
  monthDay: string
  cronExpression: string
  scheduledAt: string
}

export interface ScheduleRequestOptions {
  name: string
  inputMessage: string
  conversationPolicy: ConversationPolicy
  targetConversationId: string
  maxRuns: string
  endAt: string
  autoPauseAfterFailures: string
  maxRunsLimited: boolean
  autoPauseEnabled: boolean
}

export const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
export const WEEKDAY_CRON = [1, 2, 3, 4, 5, 6, 0] as const
export const INTERVAL_OPTIONS = [5, 10, 20, 30, 60] as const
export const DEFAULT_SCHEDULE_INPUT_MESSAGE = 'Cron trigger fired.'

export function toDateTimeLocal(value: string | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const offsetMs = date.getTimezoneOffset() * 60 * 1000
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16)
}

export function defaultScheduledAt(now = new Date()): string {
  const date = new Date(now)
  date.setHours(date.getHours() + 1, 0, 0, 0)
  return toDateTimeLocal(date.toISOString())
}

export function getDefaultScheduleFormState(now = new Date()): ScheduleFormState {
  return {
    type: 'minutes',
    intervalMinutes: 10,
    hour: '09',
    minute: '00',
    weekdays: new Set<number>(),
    monthDay: '1',
    cronExpression: '',
    scheduledAt: defaultScheduledAt(now),
  }
}

export function parseTriggerToForm(trigger: AgentTrigger, now = new Date()): ScheduleFormState {
  const config = trigger.schedule_config ?? {}

  if (trigger.trigger_type === 'one_time') {
    return {
      ...getDefaultScheduleFormState(now),
      type: 'one_time',
      scheduledAt: toDateTimeLocal(config.scheduled_at),
    }
  }

  if (trigger.trigger_type === 'interval') {
    return {
      ...getDefaultScheduleFormState(now),
      type: 'minutes',
      intervalMinutes: config.interval_minutes ?? 10,
    }
  }

  const cron = config.cron_expression ?? ''
  const parts = cron.split(' ')

  if (parts.length === 5) {
    const [cronMin, cronHour, cronDom, , cronDow] = parts
    const hour = cronHour.padStart(2, '0')
    const minute = cronMin.padStart(2, '0')

    if (cronDow !== '*') {
      return {
        ...getDefaultScheduleFormState(now),
        type: 'weekly',
        hour,
        minute,
        weekdays: new Set(cronDow.split(',').map(Number)),
        cronExpression: cron,
      }
    }

    if (cronDom !== '*') {
      return {
        ...getDefaultScheduleFormState(now),
        type: 'monthly',
        hour,
        minute,
        monthDay: cronDom,
        cronExpression: cron,
      }
    }

    return {
      ...getDefaultScheduleFormState(now),
      type: 'daily',
      hour,
      minute,
      cronExpression: cron,
    }
  }

  return {
    ...getDefaultScheduleFormState(now),
    type: 'advanced',
    cronExpression: cron,
  }
}

export function buildScheduleRequest(
  form: ScheduleFormState,
  options: ScheduleRequestOptions,
): TriggerCreateRequest {
  switch (form.type) {
    case 'minutes':
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'interval',
          schedule_config: { interval_minutes: form.intervalMinutes },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
    case 'one_time':
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'one_time',
          schedule_config: { scheduled_at: new Date(form.scheduledAt).toISOString() },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
    case 'daily':
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * *` },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
    case 'weekly': {
      const days = Array.from(form.weekdays).sort().join(',') || '*'
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: `${form.minute} ${form.hour} * * ${days}` },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
    }
    case 'monthly':
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: {
            cron_expression: `${form.minute} ${form.hour} ${form.monthDay} * *`,
          },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
    case 'advanced':
      return withCommonOptions(
        {
          name: options.name.trim() || undefined,
          trigger_type: 'cron',
          schedule_config: { cron_expression: form.cronExpression.trim() },
          input_message: options.inputMessage.trim() || DEFAULT_SCHEDULE_INPUT_MESSAGE,
          timezone: 'Asia/Seoul',
        },
        options,
      )
  }
}

function withCommonOptions(
  req: TriggerCreateRequest,
  options: ScheduleRequestOptions,
): TriggerCreateRequest {
  const trimmedTarget = options.targetConversationId.trim()
  const parsedMaxRuns =
    options.maxRunsLimited && options.maxRuns.trim() ? Number(options.maxRuns) : null
  const parsedFailures =
    options.autoPauseEnabled && options.autoPauseAfterFailures.trim()
      ? Number(options.autoPauseAfterFailures)
      : null

  return {
    ...req,
    conversation_policy: options.conversationPolicy,
    target_conversation_id:
      options.conversationPolicy === 'selected_conversation' && trimmedTarget
        ? trimmedTarget
        : null,
    max_runs: parsedMaxRuns && parsedMaxRuns > 0 ? parsedMaxRuns : null,
    end_at: options.endAt ? new Date(options.endAt).toISOString() : null,
    auto_pause_after_failures: parsedFailures && parsedFailures > 0 ? parsedFailures : null,
  }
}
