import type { AgentTrigger } from '@/lib/types'

import {
  formatScheduleCenterLabel,
  formatTriggerSummary,
  getScheduleStatusVariant,
} from './cron-labels'

const trigger: AgentTrigger = {
  id: 'trigger-1',
  agent_id: 'agent-1',
  name: 'Digest',
  trigger_type: 'cron',
  schedule_config: { cron_expression: '5 9 * * 1,3' },
  input_message: 'Run',
  timezone: 'Asia/Seoul',
  conversation_policy: 'schedule_thread',
  schedule_conversation_id: null,
  target_conversation_id: null,
  status: 'active',
  last_run_at: null,
  next_run_at: null,
  last_status: null,
  last_error: null,
  run_count: 0,
  failure_count: 0,
  max_runs: null,
  end_at: null,
  auto_pause_after_failures: null,
  created_at: '2026-06-17T00:00:00Z',
  updated_at: '2026-06-17T00:00:00Z',
}

const summaryLabels = {
  everyNMin: (minutes: number) => `Every ${minutes} minutes`,
  atTimeDays: (time: string, days: string) => `${time}, ${days}`,
  atTimeDay: (time: string, day: string) => `${time}, day ${day}`,
  atTimeEveryDay: (time: string) => `${time}, every day`,
  weekdays: {
    '0': 'Sun',
    '1': 'Mon',
    '2': 'Tue',
    '3': 'Wed',
    '4': 'Thu',
    '5': 'Fri',
    '6': 'Sat',
  },
}

describe('schedule cron labels', () => {
  it('formats a weekly cron summary with localized weekdays', () => {
    expect(formatTriggerSummary(trigger, summaryLabels)).toBe('09:05, Mon, Wed')
  })

  it('formats center labels for interval, one-time, and cron schedules', () => {
    const centerLabels = {
      interval: (minutes: number) => `Every ${minutes}m`,
      oneTime: 'One time',
      cron: 'Cron',
    }

    expect(
      formatScheduleCenterLabel(
        {
          ...trigger,
          trigger_type: 'interval',
          schedule_config: { interval_minutes: 30 },
        },
        centerLabels,
      ),
    ).toBe('Every 30m')
    expect(
      formatScheduleCenterLabel(
        {
          ...trigger,
          trigger_type: 'one_time',
          schedule_config: { scheduled_at: '2030-01-02T09:30:00Z' },
        },
        centerLabels,
      ),
    ).toBe('One time')
    expect(formatScheduleCenterLabel(trigger, centerLabels)).toBe('5 9 * * 1,3')
  })

  it('maps schedule statuses to badge variants', () => {
    expect(getScheduleStatusVariant('active')).toBe('default')
    expect(getScheduleStatusVariant('error')).toBe('destructive')
    expect(getScheduleStatusVariant('paused')).toBe('secondary')
  })
})
