import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { render, screen, userEvent, waitFor } from '../../test-utils'
import { server } from '../../setup'
import { TraceDebuggerView } from '@/components/chat/trace-debugger-view'

const API_BASE = 'http://localhost:8001'

const listResponse = {
  conversation_id: 'conv-debug',
  langfuse_enabled: true,
  fallback_reason: null,
  traces: [
    {
      trace_id: 'lf-trace-1',
      provider: 'langfuse',
      name: 'agent.chat',
      status: 'completed',
      source: 'chat',
      started_at: '2026-05-30T01:00:00Z',
      completed_at: '2026-05-30T01:00:02Z',
      duration_ms: 2048,
      total_tokens: 42,
      moldy_run_id: 'run-1',
      langfuse_url: 'https://langfuse.local/project/moldy/traces/lf-trace-1',
      fallback: false,
      fallback_reason: null,
    },
  ],
}

const detailResponse = {
  conversation_id: 'conv-debug',
  trace: listResponse.traces[0],
  spans: [
    {
      id: 'root',
      parent_id: null,
      name: 'Moldy assistant turn',
      kind: 'workflow',
      status: 'completed',
      started_at: '2026-05-30T01:00:00Z',
      ended_at: '2026-05-30T01:00:02Z',
      duration_ms: 2048,
      input: { source: 'message_events' },
      output: { content: 'done' },
      metadata: { moldy_run_id: 'run-1' },
    },
    {
      id: 'tool-1',
      parent_id: 'root',
      name: 'web_search',
      kind: 'tool',
      status: 'completed',
      started_at: '2026-05-30T01:00:01Z',
      ended_at: '2026-05-30T01:00:02Z',
      duration_ms: 1000,
      input: { query: 'moldy' },
      output: { count: 3 },
      metadata: {},
    },
  ],
  raw: null,
  fallback_reason: null,
}

describe('TraceDebuggerView', () => {
  it('renders trace list, waterfall spans, and selected span detail', async () => {
    server.use(
      http.get(`${API_BASE}/api/conversations/:conversationId/debug/traces`, () =>
        HttpResponse.json(listResponse),
      ),
      http.get(`${API_BASE}/api/conversations/:conversationId/debug/traces/:traceId`, () =>
        HttpResponse.json(detailResponse),
      ),
    )

    render(<TraceDebuggerView conversationId="conv-debug" />)

    expect(await screen.findByText('Trace debugger')).toBeInTheDocument()
    expect(await screen.findByText('AgentPrism')).toBeInTheDocument()
    expect(await screen.findByText('Langfuse')).toBeInTheDocument()
    expect((await screen.findAllByText('agent.chat')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('Moldy assistant turn')).length).toBeGreaterThan(0)
    expect(screen.getByText('web_search')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByText('web_search'))

    await waitFor(() => {
      expect(screen.getAllByText('TOOL').length).toBeGreaterThan(0)
      expect(screen.getAllByText('In/Out').length).toBeGreaterThan(0)
    })
  })

  it('shows message_events fallback when Langfuse detail is unavailable', async () => {
    server.use(
      http.get(`${API_BASE}/api/conversations/:conversationId/debug/traces`, () =>
        HttpResponse.json({
          ...listResponse,
          langfuse_enabled: false,
          fallback_reason: 'Langfuse disabled',
        }),
      ),
      http.get(`${API_BASE}/api/conversations/:conversationId/debug/traces/:traceId`, () =>
        HttpResponse.json({
          ...detailResponse,
          fallback_reason: 'Langfuse disabled',
          trace: { ...detailResponse.trace, fallback: true },
        }),
      ),
    )

    render(<TraceDebuggerView conversationId="conv-debug" />)

    expect(await screen.findByText('Langfuse disabled')).toBeInTheDocument()
    expect(await screen.findByText('message_events fallback')).toBeInTheDocument()
  })
})
