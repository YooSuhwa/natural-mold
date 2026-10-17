import { describe, expect, it } from 'vitest'
import { agUiEventToMoldyEvents } from '../chat-run-consumer'

describe('agUiEventToMoldyEvents', () => {
  it('maps text lifecycle events back to Moldy SSE events', () => {
    expect(
      agUiEventToMoldyEvents(
        {
          type: 'TEXT_MESSAGE_START',
          messageId: 'run-1',
          role: 'assistant',
        },
        'run-1-1:ag:1',
      ),
    ).toEqual([
      {
        id: 'run-1-1:ag:1',
        event: 'message_start',
        data: { id: 'run-1', role: 'assistant' },
      },
    ])

    expect(
      agUiEventToMoldyEvents(
        {
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: 'run-1',
          delta: 'hello',
        },
        'run-1-2:ag:0',
      ),
    ).toEqual([{ id: 'run-1-2:ag:0', event: 'content_delta', data: { delta: 'hello' } }])

    expect(
      agUiEventToMoldyEvents(
        {
          type: 'TEXT_MESSAGE_END',
          messageId: 'run-1',
          rawEvent: {
            content: 'hello',
            usage: { prompt_tokens: 1, completion_tokens: 2 },
            status: 'completed',
          },
        },
        'run-1-3:ag:0',
      ),
    ).toEqual([
      {
        id: 'run-1-3:ag:0',
        event: 'message_end',
        data: {
          content: 'hello',
          usage: { prompt_tokens: 1, completion_tokens: 2 },
          status: 'completed',
        },
      },
    ])
  })

  it('maps tool and custom events back to Moldy SSE events', () => {
    expect(
      agUiEventToMoldyEvents(
        {
          type: 'TOOL_CALL_START',
          toolCallId: 'tc-1',
          toolCallName: 'web_search',
          rawEvent: { parameters: { query: 'moldy' } },
        },
        'run-1-4:ag:0',
      ),
    ).toEqual([
      {
        id: 'run-1-4:ag:0',
        event: 'tool_call_start',
        data: {
          tool_call_id: 'tc-1',
          tool_name: 'web_search',
          parameters: { query: 'moldy' },
        },
      },
    ])

    expect(
      agUiEventToMoldyEvents(
        {
          type: 'TOOL_CALL_RESULT',
          toolCallId: 'tc-1',
          content: 'result',
          rawEvent: { tool_name: 'web_search' },
        },
        'run-1-5:ag:0',
      ),
    ).toEqual([
      {
        id: 'run-1-5:ag:0',
        event: 'tool_call_result',
        data: {
          tool_call_id: 'tc-1',
          tool_name: 'web_search',
          result: 'result',
        },
      },
    ])

    expect(
      agUiEventToMoldyEvents(
        {
          type: 'CUSTOM',
          name: 'moldy.interrupt',
          value: {
            payload: { interrupt_id: 'hitl-1', action_requests: [], review_configs: [] },
          },
        },
        'run-1-6:ag:0',
      ),
    ).toEqual([
      {
        id: 'run-1-6:ag:0',
        event: 'interrupt',
        data: { interrupt_id: 'hitl-1', action_requests: [], review_configs: [] },
      },
    ])
  })

  it('keeps the auth_required kind and attempt of moldy.interrupt (G-05)', () => {
    const payload = {
      interrupt_id: 'auth-1',
      type: 'auth_required',
      reason: 'expired',
      items: [{ resource_name: 'Slack', credential_id: 'c1', fixable_by: 'me' }],
      attempt: 2,
      allowed_decisions: ['respond'],
    }
    expect(
      agUiEventToMoldyEvents(
        { type: 'CUSTOM', name: 'moldy.interrupt', value: { payload } },
        'run-1-8:ag:0',
      ),
    ).toEqual([{ id: 'run-1-8:ag:0', event: 'interrupt', data: payload }])
  })

  it('RUN_ERROR의 code를 보존해 actionable error 분기가 동작하게 한다', () => {
    expect(
      agUiEventToMoldyEvents(
        { type: 'RUN_ERROR', message: 'no key', code: 'llm_credential_required' },
        'run-1-7:ag:0',
      ),
    ).toEqual([
      {
        id: 'run-1-7:ag:0',
        event: 'error',
        data: { message: 'no key', code: 'llm_credential_required' },
      },
    ])
  })

  it('알 수 없는 이벤트 타입은 throw 하지 않고 빈 배열을 반환한다', () => {
    const futureEvent = { type: 'FUTURE_EVENT' } as unknown as Parameters<
      typeof agUiEventToMoldyEvents
    >[0]
    expect(agUiEventToMoldyEvents(futureEvent, 'run-1-8:ag:0')).toEqual([])
  })
})
