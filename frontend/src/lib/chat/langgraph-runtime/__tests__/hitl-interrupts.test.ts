import { AIMessage, HumanMessage } from '@langchain/core/messages'
import { describe, expect, it } from 'vitest'
import { appendInterruptToolCallMessages, standardPayloadFromInterrupt } from '../hitl-interrupts'

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
      payload: {
        action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
        review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve'] }],
      },
    })

    expect(payload).toEqual({
      interrupt_id: 'intr-live',
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
})
