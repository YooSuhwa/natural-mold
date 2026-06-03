import { describe, expect, it } from 'vitest'
import type { Message } from '@/lib/types'
import { compactDeepResearchMessages } from './deep-research-summary'

function message(overrides: Partial<Message>): Message {
  return {
    id: 'm',
    conversation_id: 'c',
    role: 'assistant',
    content: '',
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-06-03T00:00:00.000Z',
    ...overrides,
  }
}

describe('compactDeepResearchMessages', () => {
  it('leaves a single tavily_search call as a normal search tool', () => {
    const messages: Message[] = [
      message({
        id: 'a1',
        tool_calls: [{ id: 'call-a', name: 'tavily_search', args: { query: 'A' } }],
      }),
      message({
        id: 't1',
        role: 'tool',
        tool_call_id: 'call-a',
        content: '{"results":[{"title":"A","url":"https://a.example"}]}',
      }),
    ]

    expect(compactDeepResearchMessages(messages)).toEqual(messages)
  })

  it('replaces repeated tavily_search calls with one deep_research_summary tool call', () => {
    const messages: Message[] = [
      message({ id: 'u1', role: 'user', content: 'Agentic OS authorization architecture spec' }),
      message({
        id: 'a1',
        tool_calls: [
          { id: 'call-a', name: 'tavily_search', args: { query: 'agentic os auth' } },
          { id: 'call-b', name: 'tavily_search', args: { query: 'agentic os policy' } },
          { id: 'call-c', name: 'web_search', args: { query: 'keep me' } },
        ],
      }),
      message({
        id: 't1',
        role: 'tool',
        tool_call_id: 'call-a',
        content:
          '{"results":[{"title":"A","url":"https://docs.example/a"},{"title":"B","url":"https://blog.example/b"}]}',
      }),
      message({
        id: 't2',
        role: 'tool',
        tool_call_id: 'call-b',
        content:
          '{"results":[{"title":"A again","url":"https://docs.example/a"},{"title":"C","url":"https://news.example/c"}]}',
      }),
      message({ id: 't3', role: 'tool', tool_call_id: 'call-c', content: 'kept result' }),
    ]

    const compacted = compactDeepResearchMessages(messages)
    const assistant = compacted.find((item) => item.role === 'assistant')
    const toolMessages = compacted.filter((item) => item.role === 'tool')

    expect(assistant?.tool_calls).toEqual([
      expect.objectContaining({
        id: 'deep-research-a1',
        name: 'deep_research_summary',
        args: expect.objectContaining({
          title: 'Agentic OS authorization architecture spec',
          total_count: 2,
          completed_count: 2,
          source_count: 3,
        }),
      }),
      { id: 'call-c', name: 'web_search', args: { query: 'keep me' } },
    ])
    expect(toolMessages.map((item) => item.tool_call_id)).toEqual(['deep-research-a1', 'call-c'])
    expect(toolMessages[0]?.content).toContain('"source_count":3')
  })

  it('groups repeated tavily_search calls across one assistant turn', () => {
    const messages: Message[] = [
      message({ id: 'u1', role: 'user', content: 'Agentic OS authorization architecture spec' }),
      message({
        id: 'a1',
        tool_calls: [{ id: 'call-a', name: 'tavily_search', args: { query: 'agentic os auth' } }],
      }),
      message({
        id: 't1',
        role: 'tool',
        tool_call_id: 'call-a',
        content: '{"results":[{"title":"A","url":"https://docs.example/a"}]}',
      }),
      message({
        id: 'a2',
        tool_calls: [
          { id: 'call-b', name: 'tavily_search', args: { query: 'agentic os security' } },
        ],
      }),
      message({
        id: 't2',
        role: 'tool',
        tool_call_id: 'call-b',
        content: '{"results":[{"title":"B","url":"https://security.example/b"}]}',
      }),
      message({
        id: 'a3',
        content: '요청한 authorization architecture 자료를 정리했습니다.',
      }),
    ]

    const compacted = compactDeepResearchMessages(messages)
    const assistantMessages = compacted.filter((item) => item.role === 'assistant')
    const toolMessages = compacted.filter((item) => item.role === 'tool')

    expect(assistantMessages).toHaveLength(2)
    expect(assistantMessages[0]?.id).toBe('a1')
    expect(assistantMessages[0]?.tool_calls).toEqual([
      expect.objectContaining({
        id: 'deep-research-a1',
        name: 'deep_research_summary',
        args: expect.objectContaining({
          total_count: 2,
          completed_count: 2,
          source_count: 2,
        }),
      }),
    ])
    expect(assistantMessages[1]?.id).toBe('a3')
    expect(toolMessages.map((item) => item.tool_call_id)).toEqual(['deep-research-a1'])
  })
})
