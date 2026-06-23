import { AIMessage, HumanMessage, ToolMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import {
  activeInterruptPayloads,
  appendInterruptToolCallMessages,
  appendResolvedInterruptToolCallMessages,
  interruptPayloadResolvedByMessages,
  standardPayloadFromInterrupt,
  standardPayloadsFromInterrupts,
} from '../hitl-interrupts'
import type { StandardInterruptPayload } from '@/lib/types'

describe('standardPayloadFromInterrupt', () => {
  it('normalizes snake_case HITL interrupt values', () => {
    const payload = standardPayloadFromInterrupt({
      id: 'intr-1',
      value: {
        action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
        review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
      },
    })

    expect(payload).toEqual({
      interrupt_id: 'intr-1',
      action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
      review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
    })
  })

  it('normalizes camelCase HITL interrupt values from LangGraph React', () => {
    const payload = standardPayloadFromInterrupt({
      id: 'intr-camel',
      value: {
        actionRequests: [{ name: 'ask_user', args: { question: '계속할까요?' } }],
        reviewConfigs: [{ actionName: 'ask_user', allowedDecisions: ['respond'] }],
      },
    })

    expect(payload).toEqual({
      interrupt_id: 'intr-camel',
      action_requests: [{ name: 'ask_user', args: { question: '계속할까요?' } }],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    })
  })

  it('normalizes live input.requested interrupts from LangGraph React', () => {
    const payload = standardPayloadFromInterrupt({
      interruptId: 'intr-live',
      namespace: ['tools:call-1'],
      payload: {
        action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
        review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
      },
    })

    expect(payload).toEqual({
      interrupt_id: 'intr-live',
      namespace: ['tools:call-1'],
      action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
      review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
    })
  })

  it('adapts native ask_user interrupt values', () => {
    const payload = standardPayloadFromInterrupt({
      id: 'intr-ask',
      value: { type: 'ask_user', question: '어느 쪽?', options: ['A', 'B'] },
    })

    expect(payload).toEqual({
      interrupt_id: 'intr-ask',
      action_requests: [
        {
          name: 'ask_user',
          args: { question: '어느 쪽?', options: ['A', 'B'] },
        },
      ],
      review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
    })
  })

  it('keeps the thread namespace when root and thread interrupts share an id', () => {
    const payloads = standardPayloadsFromInterrupts([
      {
        id: 'intr-1',
        value: {
          action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
        },
      },
      {
        interruptId: 'intr-1',
        namespace: ['tools:call-1'],
        payload: {
          action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
        },
      },
    ])

    expect(payloads).toEqual([
      {
        interrupt_id: 'intr-1',
        namespace: ['tools:call-1'],
        action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
        review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
      },
    ])
  })
})

describe('appendInterruptToolCallMessages', () => {
  it('appends synthetic assistant tool calls for visible approval cards', () => {
    const messages = [new HumanMessage({ id: 'user-1', content: 'send it' })]
    const projected = appendInterruptToolCallMessages(messages, [
      {
        interrupt_id: 'intr-1',
        action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
        review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
      },
    ])
    const synthetic = projected[1]

    expect(projected).toHaveLength(2)
    expect(synthetic).toBeInstanceOf(AIMessage)
    expect(AIMessage.isInstance(synthetic) ? synthetic.tool_calls : []).toEqual([
      {
        id: 'intr-1:0',
        name: 'request_approval',
        args: expect.objectContaining({
          approval_id: 'intr-1:0',
          hitl_interrupt_id: 'intr-1',
          tool_name: 'send_email',
        }),
      },
    ])
  })

  it('does not append duplicate synthetic calls when they already exist', () => {
    const existing = new AIMessage({
      id: 'existing',
      content: '',
      tool_calls: [{ id: 'intr-1:0', name: 'request_approval', args: {} }],
    })
    const projected = appendInterruptToolCallMessages(
      [existing],
      [
        {
          interrupt_id: 'intr-1',
          action_requests: [{ name: 'send_email', args: {} }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
        },
      ],
    )

    expect(projected).toEqual([existing])
  })

  it('hydrates the persisted ask_user tool call instead of appending a duplicate interrupt card', () => {
    const existing = new AIMessage({
      id: 'assistant-ask',
      content: [
        {
          type: 'tool_call',
          id: 'toolu-ask',
          name: 'ask_user',
          args: {
            mode: 'option_list',
            question: '과일을 골라주세요',
            options: ['사과', '포도', '배'],
          },
        },
      ],
      tool_calls: [
        {
          id: 'toolu-ask',
          name: 'ask_user',
          args: {
            mode: 'option_list',
            question: '과일을 골라주세요',
            options: ['사과', '포도', '배'],
          },
        },
      ],
    })
    const projected = appendInterruptToolCallMessages(
      [new HumanMessage({ id: 'user-1', content: 'ask user 해줘' }), existing],
      [
        {
          interrupt_id: 'intr-ask',
          action_requests: [
            {
              name: 'ask_user',
              args: {
                mode: 'option_list',
                question: '과일을 골라주세요',
                options: ['사과', '포도', '배'],
              },
            },
          ],
          review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
        },
      ],
    )

    expect(projected).toHaveLength(2)
    expect(AIMessage.isInstance(projected[1]) ? projected[1].tool_calls : []).toEqual([
      expect.objectContaining({
        id: 'toolu-ask',
        name: 'ask_user',
        args: expect.objectContaining({
          approval_id: 'toolu-ask',
          hitl_interrupt_id: 'intr-ask',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        }),
      }),
    ])
  })

  it('hydrates a persisted ask_user tool call even when the interrupt id already matches', () => {
    const existing = new AIMessage({
      id: 'assistant-ask',
      content: [
        {
          type: 'tool_call',
          id: 'intr-ask:0',
          name: 'ask_user',
          args: {
            mode: 'option_list',
            question: '과일을 골라주세요',
            options: ['사과', '포도', '배'],
          },
        },
      ],
      tool_calls: [
        {
          id: 'intr-ask:0',
          name: 'ask_user',
          args: {
            mode: 'option_list',
            question: '과일을 골라주세요',
            options: ['사과', '포도', '배'],
          },
        },
      ],
    })
    const projected = appendInterruptToolCallMessages(
      [new HumanMessage({ id: 'user-1', content: 'ask user 해줘' }), existing],
      [
        {
          interrupt_id: 'intr-ask',
          action_requests: [
            {
              name: 'ask_user',
              args: {
                mode: 'option_list',
                question: '과일을 골라주세요',
                options: ['사과', '포도', '배'],
              },
            },
          ],
          review_configs: [{ action_name: 'ask_user', allowed_decisions: ['respond'] }],
        },
      ],
    )

    expect(projected).toHaveLength(2)
    expect(projected[1]).not.toBe(existing)
    expect(projected[1]).toMatchObject({
      status: { type: 'requires-action', reason: 'tool-calls' },
    })
    expect(AIMessage.isInstance(projected[1]) ? projected[1].tool_calls : []).toEqual([
      expect.objectContaining({
        id: 'intr-ask:0',
        name: 'ask_user',
        args: expect.objectContaining({
          approval_id: 'intr-ask:0',
          hitl_interrupt_id: 'intr-ask',
          hitl_action_index: 0,
          hitl_total_actions: 1,
        }),
      }),
    ])
  })
})

describe('appendResolvedInterruptToolCallMessages', () => {
  it('does not append a synthetic completed ask_user card when the persisted ask_user call is visible', () => {
    const existing = new AIMessage({
      id: 'assistant-ask',
      content: '',
      tool_calls: [
        {
          id: 'toolu-ask',
          name: 'ask_user',
          args: {
            mode: 'option_list',
            question: '과일을 골라주세요',
            options: ['사과', '포도', '배'],
          },
        },
      ],
    })

    const projected = appendResolvedInterruptToolCallMessages(
      [existing],
      [
        {
          toolCall: {
            id: 'intr-ask:0',
            name: 'ask_user',
            args: {
              mode: 'option_list',
              question: '과일을 골라주세요',
              options: ['사과', '포도', '배'],
              hitl_interrupt_id: 'intr-ask',
            },
          },
          result: { decision: 'approved' },
        },
      ],
    )

    expect(projected).toEqual([existing])
  })
})

describe('interruptPayloadResolvedByMessages', () => {
  const payload: StandardInterruptPayload = {
    interrupt_id: 'intr-docx',
    action_requests: [
      {
        name: 'execute_in_skill',
        args: {
          skill_directory: '/skills/docx-document',
          command: 'node scripts/create_langgraph_v3_artifacts.cjs',
        },
      },
    ],
    review_configs: [{ action_name: 'execute_in_skill', allowed_decisions: ['approve'] }],
  }

  it('treats a replayed interrupt as resolved when its real tool result is present', () => {
    const messages = [
      new AIMessage({
        id: 'assistant-tool',
        content: '',
        tool_calls: [
          {
            id: 'call-docx',
            name: 'execute_in_skill',
            args: payload.action_requests[0].args,
          },
        ],
      }),
      new ToolMessage({
        id: 'tool-docx',
        content: 'done',
        tool_call_id: 'call-docx',
      }),
    ]

    expect(interruptPayloadResolvedByMessages(payload, messages)).toBe(true)
  })

  it('keeps an interrupt active before the requested tool result exists', () => {
    const messages = [
      new AIMessage({
        id: 'assistant-tool',
        content: '',
        tool_calls: [
          {
            id: 'call-docx',
            name: 'execute_in_skill',
            args: payload.action_requests[0].args,
          },
        ],
      }),
    ]

    expect(interruptPayloadResolvedByMessages(payload, messages)).toBe(false)
  })

  it('filters replayed interrupts whose real tool result is already in messages', () => {
    const messages = [
      new AIMessage({
        id: 'assistant-tool',
        content: '',
        tool_calls: [
          {
            id: 'call-docx',
            name: 'execute_in_skill',
            args: payload.action_requests[0].args,
          },
        ],
      }),
      new ToolMessage({
        id: 'tool-docx',
        content: 'done',
        tool_call_id: 'call-docx',
      }),
    ]

    expect(activeInterruptPayloads([payload], messages, [])).toEqual([])
  })
})
