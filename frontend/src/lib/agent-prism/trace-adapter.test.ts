import { describe, expect, it } from 'vitest'

import { toAgentPrismTraceViewerData } from './trace-adapter'
import type { DebugTraceDetailResponse, DebugTraceSummary } from '@/lib/types'

describe('toAgentPrismTraceViewerData', () => {
  it('prefers backend-normalized spans over raw rows for detail input/output', () => {
    const trace: DebugTraceSummary = {
      trace_id: 'trace-1',
      provider: 'langfuse',
      name: 'agent.chat',
      status: 'completed',
      source: 'chat',
      started_at: '2026-05-31T10:00:00Z',
      completed_at: '2026-05-31T10:00:01Z',
      duration_ms: 1000,
      total_tokens: 10,
      moldy_run_id: 'run-1',
      langfuse_url: null,
      fallback: false,
      fallback_reason: null,
    }
    const detail: DebugTraceDetailResponse = {
      conversation_id: 'conv-1',
      trace,
      spans: [
        {
          id: 'span-1',
          parent_id: null,
          name: 'Moldy assistant turn',
          kind: 'workflow',
          status: 'completed',
          started_at: trace.started_at,
          ended_at: trace.completed_at,
          duration_ms: trace.duration_ms,
          input: { messages: [{ role: 'user', content: 'debug this trace' }] },
          output: { content: 'done' },
          metadata: {},
        },
      ],
      raw: [
        {
          id: 'raw-1',
          input: { provider: 'message_events' },
        },
      ],
      fallback_reason: null,
    }

    const [viewerTrace] = toAgentPrismTraceViewerData([trace], [detail])

    expect(viewerTrace.spans).toHaveLength(1)
    expect(viewerTrace.spans[0].input).toBe('debug this trace')
    expect(viewerTrace.spans[0].output).toBe('done')
    expect(viewerTrace.spans[0].input).not.toContain('"provider": "message_events"')
  })

  it('formats LangChain message state as plain input and output text', () => {
    const trace: DebugTraceSummary = {
      trace_id: 'trace-2',
      provider: 'langfuse',
      name: 'agent.chat',
      status: 'completed',
      source: 'chat',
      started_at: '2026-05-31T10:00:00Z',
      completed_at: '2026-05-31T10:00:01Z',
      duration_ms: 1000,
      total_tokens: 10,
      moldy_run_id: 'run-2',
      langfuse_url: null,
      fallback: false,
      fallback_reason: null,
    }
    const detail: DebugTraceDetailResponse = {
      conversation_id: 'conv-1',
      trace,
      spans: [
        {
          id: 'span-1',
          parent_id: null,
          name: 'agent',
          kind: 'agent',
          status: 'completed',
          started_at: trace.started_at,
          ended_at: trace.completed_at,
          duration_ms: trace.duration_ms,
          input: {
            messages: [
              { type: 'human', content: 'first question' },
              { type: 'ai', content: 'first answer' },
              { type: 'human', content: 'current question' },
            ],
          },
          output: {
            messages: [
              { type: 'human', content: 'current question' },
              { type: 'ai', content: 'current answer' },
            ],
          },
          metadata: {},
        },
      ],
      raw: null,
      fallback_reason: null,
    }

    const [viewerTrace] = toAgentPrismTraceViewerData([trace], [detail])

    expect(viewerTrace.spans[0].input).toBe('current question')
    expect(viewerTrace.spans[0].output).toBe('current answer')
  })

  it('formats stringified LangChain message state as plain text', () => {
    const trace: DebugTraceSummary = {
      trace_id: 'trace-3',
      provider: 'langfuse',
      name: 'agent.chat',
      status: 'completed',
      source: 'chat',
      started_at: '2026-05-31T10:00:00Z',
      completed_at: '2026-05-31T10:00:01Z',
      duration_ms: 1000,
      total_tokens: 10,
      moldy_run_id: 'run-3',
      langfuse_url: null,
      fallback: false,
      fallback_reason: null,
    }
    const state = {
      messages: [
        { type: 'human', content: 'previous question' },
        { type: 'ai', content: 'previous answer' },
        { type: 'human', content: 'selected trace question' },
        { type: 'ai', content: 'selected trace answer' },
      ],
      skills_metadata: [{ name: 'image-generation' }],
    }
    const detail: DebugTraceDetailResponse = {
      conversation_id: 'conv-1',
      trace,
      spans: [
        {
          id: 'span-1',
          parent_id: null,
          name: 'agent',
          kind: 'agent',
          status: 'completed',
          started_at: trace.started_at,
          ended_at: trace.completed_at,
          duration_ms: trace.duration_ms,
          input: JSON.stringify(state),
          output: JSON.stringify(state),
          metadata: {},
        },
      ],
      raw: null,
      fallback_reason: null,
    }

    const [viewerTrace] = toAgentPrismTraceViewerData([trace], [detail])

    expect(viewerTrace.spans[0].input).toBe('selected trace question')
    expect(viewerTrace.spans[0].output).toBe('selected trace answer')
    expect(viewerTrace.spans[0].output).not.toContain('skills_metadata')
  })

  it('hides duplicate Langfuse trace shell root when runtime root exists', () => {
    const trace: DebugTraceSummary = {
      trace_id: 'trace-4',
      provider: 'langfuse',
      name: 'agent.chat',
      status: 'completed',
      source: 'chat',
      started_at: '2026-05-31T10:00:00Z',
      completed_at: '2026-05-31T10:00:03Z',
      duration_ms: 3000,
      total_tokens: 10,
      moldy_run_id: 'run-4',
      langfuse_url: null,
      fallback: false,
      fallback_reason: null,
    }
    const detail: DebugTraceDetailResponse = {
      conversation_id: 'conv-1',
      trace,
      spans: [
        {
          id: 'runtime-root',
          parent_id: null,
          name: 'agent_eca0d06b',
          kind: 'agent',
          status: 'completed',
          started_at: trace.started_at,
          ended_at: trace.completed_at,
          duration_ms: trace.duration_ms,
          input: { messages: [{ type: 'human', content: 'hello' }] },
          output: { messages: [{ type: 'ai', content: 'hi' }] },
          metadata: {},
        },
        {
          id: 'trace-shell-root',
          parent_id: null,
          name: 'agent.chat',
          kind: 'span',
          status: 'completed',
          started_at: trace.started_at,
          ended_at: trace.completed_at,
          duration_ms: trace.duration_ms,
          input: { messages: [{ type: 'human', content: 'hello' }] },
          output: null,
          metadata: {},
        },
      ],
      raw: null,
      fallback_reason: null,
    }

    const [viewerTrace] = toAgentPrismTraceViewerData([trace], [detail])

    expect(viewerTrace.spans).toHaveLength(1)
    expect(viewerTrace.spans[0].id).toBe('runtime-root')
  })
})
