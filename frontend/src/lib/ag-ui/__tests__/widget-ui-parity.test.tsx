import { act, fireEvent } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, it, vi } from 'vitest'
import { API_BASE } from '@/lib/api/client'
import { server } from '../../../../tests/setup'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import { AgUiTestChat, conversationId } from '../../../../tests/ag-ui/chat-ui-harness'
import {
  bindingId,
  createFixtureAgent,
  physicalRunId,
  widgetSourceCallId,
  widgetEvents,
} from '../../../../tests/ag-ui/chat-ui-fixtures'

const resourceUri = 'ui://reports/receipt.html'
const structuredContent = { category: 'receipt-widget-only' }
const resultMeta = { displayKey: 'example-widget-metadata' }

afterEach(() => vi.restoreAllMocks())

async function readFrameOutput(frame: HTMLIFrameElement) {
  const frameWindow = frame.contentWindow
  if (!frameWindow) throw new Error('The mounted widget has no window')
  const origin = new URL(frame.src).origin
  const post = vi.spyOn(frameWindow, 'postMessage')
  const notify = (data: Record<string, unknown>) =>
    window.dispatchEvent(new MessageEvent('message', { origin, source: frameWindow, data }))

  try {
    await act(async () => {
      fireEvent.load(frame)
    })
    notify({ jsonrpc: '2.0', id: 'start-widget', method: 'ui/initialize', params: {} })
    await waitFor(() =>
      expect(post.mock.calls.map(([message]) => message)).toContainEqual(
        expect.objectContaining({ id: 'start-widget', result: expect.any(Object) }),
      ),
    )
    notify({ jsonrpc: '2.0', method: 'ui/notifications/initialized' })
    await waitFor(() =>
      expect(post.mock.calls.map(([message]) => message)).toContainEqual(
        expect.objectContaining({
          method: 'ui/notifications/tool-result',
          params: expect.objectContaining({ structuredContent, _meta: resultMeta }),
        }),
      ),
    )
  } finally {
    post.mockRestore()
  }
}

it('hydrates the bound MCP widget for its viewer and rechecks access when the messages reopen', async () => {
  const run = `${API_BASE}/api/conversations/${conversationId}/runs/${physicalRunId}`
  const endpoint = `${run}/mcp-apps/${encodeURIComponent(widgetSourceCallId)}`
  let allowed = true
  const bindings: (string | null)[] = []
  const resourceReads: unknown[] = []

  server.use(
    http.get(endpoint, ({ request }) => {
      bindings.push(new URL(request.url).searchParams.get('binding_id'))
      if (!allowed) return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
      return HttpResponse.json({
        mcp_app: {
          version: 1,
          run_id: physicalRunId,
          binding_id: bindingId,
          resource_uri: resourceUri,
          tool_meta: { ui: { resourceUri } },
          result_meta: resultMeta,
        },
        structured_content: structuredContent,
      })
    }),
    http.post(endpoint, async ({ request }) => {
      resourceReads.push(await request.json())
      return HttpResponse.json({
        uri: resourceUri,
        mimeType: 'text/html;profile=mcp-app',
        html: '<html><body>Receipt viewer</body></html>',
      })
    }),
  )

  let firstRun = true
  const {
    agent,
    session,
    fetch: runFetch,
  } = createFixtureAgent((runId) => {
    if (!firstRun) return []
    firstRun = false
    return widgetEvents(runId)
  })
  const user = userEvent.setup()
  const chat = render(<AgUiTestChat agent={agent} session={session} />)
  await user.click(screen.getByRole('button', { name: 'Send fixture' }))
  const iframe = await waitFor(() => {
    const frame = chat.container.querySelector('iframe')
    if (!frame) throw new Error('Expected the official MCP widget host to mount')
    return frame
  })
  expect(session.getSnapshot().activities).toContainEqual(
    expect.objectContaining({
      activityType: 'moldy.mcp_app',
      content: expect.objectContaining({
        public_tool_call_id: expect.stringMatching(/^tool-[a-f0-9]{64}$/),
        tool_call_id: widgetSourceCallId,
      }),
    }),
  )
  expect(bindings).toContain(bindingId)
  expect(resourceReads).toContainEqual(
    expect.objectContaining({ method: 'mcp-apps/read-resource' }),
  )
  await readFrameOutput(iframe)

  await user.click(screen.getByRole('button', { name: 'Send fixture' }))
  await waitFor(() => expect(runFetch).toHaveBeenCalledTimes(2))
  const nextBody = String(runFetch.mock.calls[1][1]?.body)
  expect(JSON.parse(nextBody)).toMatchObject({
    messages: [expect.objectContaining({ role: 'user' })],
  })
  expect(nextBody).toContain('Summarize the records')
  expect(nextBody).not.toContain(structuredContent.category)
  expect(nextBody).not.toContain(resultMeta.displayKey)

  chat.rerender(<AgUiTestChat agent={agent} session={session} messagesVisible={false} />)
  expect(chat.container.querySelector('iframe')).toBeNull()
  const priorReads = bindings.length
  const priorResourceReads = resourceReads.length
  allowed = false
  chat.rerender(<AgUiTestChat agent={agent} session={session} />)
  await waitFor(() => expect(bindings.length).toBeGreaterThan(priorReads))
  expect(bindings.every((value) => value === bindingId)).toBe(true)
  expect(await screen.findByText('report_view')).toBeVisible()
  expect(chat.container.querySelector('iframe')).toBeNull()
  expect(resourceReads).toHaveLength(priorResourceReads)
  expect(runFetch).toHaveBeenCalledTimes(2)
})
