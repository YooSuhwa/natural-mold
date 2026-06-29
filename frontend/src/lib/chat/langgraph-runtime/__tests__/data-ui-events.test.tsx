import { act, renderHook } from '@testing-library/react'
import { AIMessage } from '@langchain/core/messages'
import type { AnyStream } from '@langchain/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { UIDataEventPayload, UIDataItem } from '@/lib/types/ui-data'
import {
  attachDataUIToMessages,
  protocolUIDataPayload,
  useLangGraphDataUIEffects,
} from '../data-ui-events'

const mocks = vi.hoisted(() => ({
  useChannelEffect: vi.fn(),
}))

vi.mock('@langchain/react', () => ({
  useChannelEffect: mocks.useChannelEffect,
}))

type ChannelEffectOptions = {
  replay?: boolean
  onEvent: (event: unknown) => void
}

function payload(overrides: Partial<UIDataEventPayload> = {}): UIDataEventPayload {
  return {
    schema_version: 1,
    type: 'demo_note',
    message_id: null,
    run_id: 'assistant-1',
    tool_call_id: 'call-1',
    props: { text: 'demo note' },
    ...overrides,
  }
}

function protocolEvent(p: UIDataEventPayload) {
  return {
    type: 'event',
    method: 'custom',
    event_id: 'event-ui-data-1',
    seq: 9,
    run_id: 'run-1',
    params: { namespace: [], data: { name: 'ui_data', payload: p } },
  }
}

describe('protocolUIDataPayload', () => {
  it('unwraps named custom ui_data payloads', () => {
    const p = payload()
    expect(
      protocolUIDataPayload({
        method: 'custom',
        params: { data: { name: 'ui_data', payload: p } },
      }),
    ).toEqual(p)
    expect(protocolUIDataPayload(protocolEvent(p))).toEqual(p)
  })

  it('ignores other custom channels (e.g. file_event)', () => {
    expect(
      protocolUIDataPayload({
        method: 'custom',
        params: { data: { name: 'file_event', payload: payload() } },
      }),
    ).toBeNull()
  })

  it('rejects payloads missing type or props', () => {
    expect(
      protocolUIDataPayload({
        method: 'custom',
        params: { data: { name: 'ui_data', payload: { props: {} } } },
      }),
    ).toBeNull()
    expect(
      protocolUIDataPayload({
        method: 'custom',
        params: { data: { name: 'ui_data', payload: { type: 'demo_note' } } },
      }),
    ).toBeNull()
  })
})

describe('attachDataUIToMessages', () => {
  const item = (toolCallId: string | null): UIDataItem => ({
    type: 'demo_note',
    props: { text: 'x' },
    tool_call_id: toolCallId,
  })
  const caller = (id: string, toolCallId: string) =>
    new AIMessage({ id, content: '', tool_calls: [{ id: toolCallId, name: 't', args: {} }] })

  it('attaches by tool_call_id to the assistant message that made the call', () => {
    const messages = [caller('a1', 'call-1'), new AIMessage({ id: 'a2', content: 'done' })]
    const result = attachDataUIToMessages(messages, { 'run-1': [item('call-1')] })
    expect((result[0] as { uiData?: UIDataItem[] }).uiData).toEqual([item('call-1')])
    expect((result[1] as { uiData?: UIDataItem[] }).uiData).toBeUndefined()
  })

  it('does not collapse multi-turn ui_data onto the last assistant bubble', () => {
    const messages = [caller('a1', 'call-1'), caller('a2', 'call-2')]
    const result = attachDataUIToMessages(messages, {
      'run-1': [item('call-1')],
      'run-2': [item('call-2')],
    })
    expect((result[0] as { uiData?: UIDataItem[] }).uiData).toEqual([item('call-1')])
    expect((result[1] as { uiData?: UIDataItem[] }).uiData).toEqual([item('call-2')])
  })

  it('falls back to the last assistant message when no tool call matches', () => {
    const message = new AIMessage({ id: 'a1', content: 'hi' })
    const result = attachDataUIToMessages([message], { 'run-xyz': [item(null)] })
    expect((result[0] as { uiData?: UIDataItem[] }).uiData).toEqual([item(null)])
  })

  it('returns messages unchanged when there are no items', () => {
    const message = new AIMessage({ id: 'a1', content: 'hi' })
    const result = attachDataUIToMessages([message], {})
    expect(result[0]).toBe(message)
  })
})

describe('useLangGraphDataUIEffects', () => {
  beforeEach(() => {
    mocks.useChannelEffect.mockReset()
  })

  it('ingests a ui_data custom event and attaches it to the matching message', () => {
    const stream = { kind: 'stream' } as unknown as AnyStream
    const assistantMessage = new AIMessage({ id: 'assistant-1', content: 'hi' })

    const { result } = renderHook(() =>
      useLangGraphDataUIEffects({
        stream,
        conversationId: 'conversation-1',
        messages: [assistantMessage],
      }),
    )

    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined
    expect(effectOptions).toEqual(expect.objectContaining({ replay: true }))

    act(() => {
      effectOptions?.onEvent(protocolEvent(payload()))
    })

    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toEqual([
      { type: 'demo_note', props: { text: 'demo note' }, tool_call_id: 'call-1' },
    ])
  })

  it('dedupes repeated events by event key', () => {
    const stream = { kind: 'stream' } as unknown as AnyStream
    const assistantMessage = new AIMessage({ id: 'assistant-1', content: 'hi' })

    const { result } = renderHook(() =>
      useLangGraphDataUIEffects({
        stream,
        conversationId: 'conversation-1',
        messages: [assistantMessage],
      }),
    )
    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined

    act(() => {
      effectOptions?.onEvent(protocolEvent(payload()))
      effectOptions?.onEvent(protocolEvent(payload()))
    })

    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toHaveLength(1)
  })

  it('dedupes the same tool_call_id re-delivered under a different run_id/event_id', () => {
    // A later run re-synthesizes a prior turn's tool result → same tool_call_id,
    // different run_id + event_id. Must NOT double-render.
    const stream = { kind: 'stream' } as unknown as AnyStream
    const assistantMessage = new AIMessage({ id: 'assistant-1', content: 'hi' })

    const { result } = renderHook(() =>
      useLangGraphDataUIEffects({
        stream,
        conversationId: 'conversation-1',
        messages: [assistantMessage],
      }),
    )
    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined

    act(() => {
      effectOptions?.onEvent({
        type: 'event',
        method: 'custom',
        event_id: 'event-run-A',
        seq: 9,
        params: { namespace: [], data: { name: 'ui_data', payload: payload({ run_id: 'run-A' }) } },
      })
      effectOptions?.onEvent({
        type: 'event',
        method: 'custom',
        event_id: 'event-run-B',
        seq: 14,
        params: { namespace: [], data: { name: 'ui_data', payload: payload({ run_id: 'run-B' }) } },
      })
    })

    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toHaveLength(1)
  })

  it('keeps distinct ui_data types from the same tool_call_id', () => {
    const stream = { kind: 'stream' } as unknown as AnyStream
    const caller = new AIMessage({
      id: 'a1',
      content: '',
      tool_calls: [{ id: 'call-1', name: 't', args: {} }],
    })

    const { result } = renderHook(() =>
      useLangGraphDataUIEffects({ stream, conversationId: 'conversation-1', messages: [caller] }),
    )
    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined

    act(() => {
      effectOptions?.onEvent(protocolEvent(payload({ type: 'demo_note', tool_call_id: 'call-1' })))
      effectOptions?.onEvent(protocolEvent(payload({ type: 'data_table', tool_call_id: 'call-1' })))
    })

    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toHaveLength(2)
  })

  it("re-ingests a conversation's replayed event after switching away and back", () => {
    // The hook can outlive a conversation switch (page not keyed). On re-entry the
    // backend replays the conversation's stored ui_data — a stale seen key must NOT
    // suppress it (the card would silently vanish). Regression for the review fix.
    const stream = { kind: 'stream' } as unknown as AnyStream
    const caller = new AIMessage({
      id: 'a1',
      content: '',
      tool_calls: [{ id: 'call-1', name: 't', args: {} }],
    })

    const { result, rerender } = renderHook(
      (props: { conversationId: string }) =>
        useLangGraphDataUIEffects({
          stream,
          conversationId: props.conversationId,
          messages: [caller],
        }),
      { initialProps: { conversationId: 'conv-A' } },
    )
    const latest = () =>
      mocks.useChannelEffect.mock.calls.at(-1)?.[2] as ChannelEffectOptions | undefined

    // In A: ingest tc:call-1.
    act(() =>
      latest()?.onEvent(protocolEvent(payload({ run_id: 'run-A', tool_call_id: 'call-1' }))),
    )
    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toHaveLength(1)

    // Switch to B and ingest a different tool call (resets the conversation-scoped store).
    rerender({ conversationId: 'conv-B' })
    act(() =>
      latest()?.onEvent(protocolEvent(payload({ run_id: 'run-B', tool_call_id: 'call-2' }))),
    )

    // Back to A — A's items were dropped on the switch; the replayed A event
    // (same tc:call-1) must be re-ingested, not deduped by a stale seen key.
    rerender({ conversationId: 'conv-A' })
    act(() =>
      latest()?.onEvent(protocolEvent(payload({ run_id: 'run-A', tool_call_id: 'call-1' }))),
    )
    expect((result.current[0] as { uiData?: UIDataItem[] }).uiData).toHaveLength(1)
  })
})
