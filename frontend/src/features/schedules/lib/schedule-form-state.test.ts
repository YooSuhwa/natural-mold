import type { AgentTrigger } from '@/lib/types'

import {
  buildScheduleRequest,
  getDefaultScheduleFormState,
  parseTriggerToForm,
  type ScheduleFormState,
  type ScheduleRequestOptions,
} from './schedule-form-state'

const baseTrigger: AgentTrigger = {
  id: 'trigger-1',
  agent_id: 'agent-1',
  name: 'Morning digest',
  trigger_type: 'cron',
  schedule_config: { cron_expression: '30 9 * * 1,3' },
  input_message: 'Run digest',
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

const baseOptions: ScheduleRequestOptions = {
  name: ' Weekly digest ',
  inputMessage: ' Summarize ',
  conversationPolicy: 'selected_conversation',
  targetConversationId: ' conv-1 ',
  maxRuns: '3',
  endAt: '2030-01-02T09:30',
  autoPauseAfterFailures: '2',
  maxRunsLimited: true,
  autoPauseEnabled: true,
}

function state(overrides: Partial<ScheduleFormState>): ScheduleFormState {
  return {
    ...getDefaultScheduleFormState(new Date('2026-06-17T00:00:00Z')),
    ...overrides,
  }
}

describe('schedule form state helpers', () => {
  it('parses a weekly cron trigger into form state', () => {
    const form = parseTriggerToForm(baseTrigger, new Date('2026-06-17T00:00:00Z'))

    expect(form.type).toBe('weekly')
    expect(form.hour).toBe('09')
    expect(form.minute).toBe('30')
    expect(form.weekdays).toEqual(new Set([1, 3]))
  })

  it('builds a weekly cron request with shared schedule options', () => {
    const request = buildScheduleRequest(
      state({
        type: 'weekly',
        hour: '09',
        minute: '30',
        weekdays: new Set([3, 1]),
      }),
      baseOptions,
    )

    expect(request).toMatchObject({
      name: 'Weekly digest',
      trigger_type: 'cron',
      schedule_config: { cron_expression: '30 09 * * 1,3' },
      input_message: 'Summarize',
      timezone: 'Asia/Seoul',
      conversation_policy: 'selected_conversation',
      target_conversation_id: 'conv-1',
      max_runs: 3,
      auto_pause_after_failures: 2,
    })
    expect(request.end_at).toBe(new Date('2030-01-02T09:30').toISOString())
  })

  it('falls back to the default prompt and unlimited limits', () => {
    const request = buildScheduleRequest(state({ type: 'minutes', intervalMinutes: 20 }), {
      ...baseOptions,
      inputMessage: '   ',
      conversationPolicy: 'schedule_thread',
      targetConversationId: 'conv-1',
      maxRunsLimited: false,
      autoPauseEnabled: false,
    })

    expect(request).toMatchObject({
      trigger_type: 'interval',
      schedule_config: { interval_minutes: 20 },
      input_message: 'Cron trigger fired.',
      target_conversation_id: null,
      max_runs: null,
      auto_pause_after_failures: null,
    })
  })
})
