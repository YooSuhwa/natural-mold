import { describe, expect, it, vi } from 'vitest'
import type { Decision, StandardInterruptPayload } from '@/lib/types'
import {
  createHiTLDecisionCoordinator,
  mergeInterruptToolCalls,
  standardInterruptToToolCalls,
} from '../standard-interrupt'

describe('standardInterruptToToolCalls', () => {
  it('maps ask_user respond-only action into the ask_user tool UI args', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-ask',
      action_requests: [
        {
          name: 'ask_user',
          args: { question: '어느 쪽?', options: ['A', 'B'] },
        },
      ],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    }

    expect(standardInterruptToToolCalls(payload)).toEqual([
      {
        id: 'intr-ask:0',
        name: 'ask_user',
        args: {
          question: '어느 쪽?',
          options: ['A', 'B'],
          approval_id: 'intr-ask:0',
          allowed_decisions: ['respond'],
          hitl_interrupt_id: 'intr-ask',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        },
      },
    ])
  })

  it('preserves extended ask_user args for question_flow mode', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-flow',
      action_requests: [
        {
          name: 'ask_user',
          args: {
            mode: 'question_flow',
            title: '에이전트 설정 확인',
            questions: [
              {
                id: 'tone',
                label: '답변 톤',
                type: 'single_select',
                options: [{ id: 'concise', label: '간결하게' }],
                required: true,
              },
            ],
          },
        },
      ],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    }

    expect(standardInterruptToToolCalls(payload)[0]?.args).toMatchObject({
      mode: 'question_flow',
      title: '에이전트 설정 확인',
      questions: payload.action_requests[0]?.args.questions,
      hitl_interrupt_id: 'intr-flow',
    })
  })

  it('maps approval actions into synthetic request_approval tool UI args', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-approval',
      action_requests: [
        {
          name: 'send_email',
          args: { to: 'a@example.com', subject: 'Hello' },
          description: 'Send the prepared email',
        },
      ],
      review_configs: [
        { action_name: 'send_email', allowed_decisions: ['approve', 'edit', 'reject'] },
      ],
    }

    expect(standardInterruptToToolCalls(payload)).toEqual([
      {
        id: 'intr-approval:0',
        name: 'request_approval',
        args: {
          tool_name: 'send_email',
          tool_args: { to: 'a@example.com', subject: 'Hello' },
          description: 'Send the prepared email',
          approval_id: 'intr-approval:0',
          allowed_decisions: ['approve', 'edit', 'reject'],
          hitl_interrupt_id: 'intr-approval',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        },
      },
    ])
  })

  it('preserves action order and indexes for multi-action interrupts', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-multi',
      action_requests: [
        { name: 'ask_user', args: { question: '계속할까요?' } },
        { name: 'delete_record', args: { id: 7 } },
      ],
      review_configs: [
        { action_name: 'ask_user', allowed_decisions: ['respond'] },
        { action_name: 'delete_record', allowed_decisions: ['approve', 'reject'] },
      ],
    }

    const calls = standardInterruptToToolCalls(payload)

    expect(calls.map((call) => call.id)).toEqual(['intr-multi:0', 'intr-multi:1'])
    expect(calls.map((call) => call.name)).toEqual(['ask_user', 'request_approval'])
    expect(calls.map((call) => call.args.hitl_action_index)).toEqual([0, 1])
  })

  it('replaces a pending write_file call with its approval card', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-file',
      action_requests: [
        {
          name: 'write_file',
          args: { file_path: '/runtime/today_diary.md', content: '# Today' },
          description: 'Tool execution requires approval',
        },
      ],
      review_configs: [{ action_name: 'write_file', allowed_decisions: ['approve', 'reject'] }],
    }

    const calls = mergeInterruptToolCalls(
      [{ id: 'toolu-1', name: 'write_file', args: { file_path: '/runtime/today_diary.md' } }],
      payload,
    )

    expect(calls).toEqual([
      {
        id: 'toolu-1',
        name: 'request_approval',
        args: {
          tool_name: 'write_file',
          tool_args: { file_path: '/runtime/today_diary.md', content: '# Today' },
          description: 'Tool execution requires approval',
          approval_id: 'toolu-1',
          allowed_decisions: ['approve', 'reject'],
          hitl_interrupt_id: 'intr-file',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        },
      },
    ])
  })

  it('replaces an empty streamed write_file call when interrupt carries the real args', () => {
    const payload: StandardInterruptPayload = {
      interrupt_id: 'intr-file-empty',
      action_requests: [
        {
          name: 'write_file',
          args: { file_path: '/runtime/today_diary.md', content: '# Today' },
        },
      ],
      review_configs: [{ action_name: 'write_file', allowed_decisions: ['approve', 'reject'] }],
    }

    const calls = mergeInterruptToolCalls(
      [{ id: 'toolu-1', name: 'write_file', args: {} }],
      payload,
    )

    expect(calls).toHaveLength(1)
    expect(calls[0]?.name).toBe('request_approval')
    expect(calls[0]?.args.tool_args).toEqual({
      file_path: '/runtime/today_diary.md',
      content: '# Today',
    })
  })
})

describe('createHiTLDecisionCoordinator', () => {
  it('resumes once all decisions are collected in original action order', async () => {
    const resume = vi.fn<
      (decisions: Decision[], displayText?: string, interruptId?: string | null) => Promise<void>
    >(async () => {})
    const coordinator = createHiTLDecisionCoordinator({
      totalActions: 2,
      interruptId: 'intr-multi',
      resume,
    })

    await coordinator.registerDecision(1, { type: 'reject', message: '아니요' }, '거부')
    expect(resume).not.toHaveBeenCalled()

    await coordinator.registerDecision(0, { type: 'approve' }, '승인')

    expect(resume).toHaveBeenCalledTimes(1)
    expect(resume).toHaveBeenCalledWith(
      [{ type: 'approve' }, { type: 'reject', message: '아니요' }],
      '승인 | 거부',
      'intr-multi',
    )
  })
})
