import { act } from '@testing-library/react'
import { render, waitFor } from '../../../../tests/test-utils'
import {
  AssistantRuntimeProvider,
  AuiConfig,
  MessagePrimitive,
  ThreadPrimitive,
  Tools,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMoldyMcpAppRenderer } from '@/lib/chat/mcp-apps/renderer'
import { apiFetch } from '@/lib/api/client'
import { AgUiActivityProvider } from '../activity-context'
import { MoldyAgUiBoundTool } from '../activity-widget'
import type { AgUiActivitySnapshot } from '../chat-history-types'

vi.mock('@/lib/api/client', async (original) => ({
  ...(await original<typeof import('@/lib/api/client')>()),
  apiFetch: vi.fn(),
}))

const threadA = '304d9a10-3b6c-4be0-a8a3-7efd71156e9a'
const threadB = 'b3dcf3af-d3c2-47ba-978e-fd64972afd10'
const runId = '45206ae0-c53f-4be6-9ef2-a6968ca04ab5'
const bindingId = 'bff36309-e8ad-4745-8080-1f76f33099ee'
const toolCallId = 'call/maps:1'
const publicToolCallId = 'tool-78cf7fb2-999a-55a7-adb7-cca0fd4bc4b5'
const childId = 'child-bda589ad-368b-5292-a067-7e2d08d8f1d2'
const ownerId = 'child-answer'
const uri = 'ui://maps/widget.html'
const messages: ThreadMessageLike[] = [
  {
    id: childId,
    role: 'assistant',
    content: [
      {
        type: 'tool-call',
        toolName: 'show_map',
        toolCallId: publicToolCallId,
        parentId: ownerId,
        args: { city: 'Seoul' },
        argsText: '{"city":"Seoul"}',
        result: 'Map ready',
      },
    ],
  },
]
const activities: AgUiActivitySnapshot[] = [
  {
    type: 'ACTIVITY_SNAPSHOT',
    messageId: 'map-pointer',
    activityType: 'moldy.mcp_app',
    parentMessageId: ownerId,
    subagentRunId: childId,
    content: {
      binding_id: bindingId,
      run_id: runId,
      tool_call_id: toolCallId,
      public_tool_call_id: publicToolCallId,
    },
  },
]
const source = {
  mcp_app: {
    version: 1,
    run_id: runId,
    binding_id: bindingId,
    resource_uri: uri,
    tool_meta: { ui: { resourceUri: uri } },
  },
  structured_content: { city: 'Seoul' },
}

function AssistantRow() {
  return (
    <MessagePrimitive.Root>
      <MessagePrimitive.Parts>
        {({ part }) =>
          part.type === 'tool-call' ? (
            <MoldyAgUiBoundTool toolUI={part.toolUI}>
              <span data-testid="standard-tool" />
            </MoldyAgUiBoundTool>
          ) : null
        }
      </MessagePrimitive.Parts>
    </MessagePrimitive.Root>
  )
}
const renderers = { AssistantMessage: AssistantRow, UserMessage: () => null }
function Harness({ conversationId }: { conversationId: string }) {
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: false,
    onNew: async () => {},
    convertMessage: (message) => message,
  })
  const config = AuiConfig({
    tools: Tools({
      mcpApp: createMoldyMcpAppRenderer({
        conversationId,
        fallback: <span data-testid="loading-widget" />,
      }),
    }),
  })
  return (
    <AgUiActivityProvider conversationId={conversationId} activities={activities}>
      <AssistantRuntimeProvider runtime={runtime} config={config}>
        <ThreadPrimitive.Root>
          <ThreadPrimitive.Messages components={renderers} />
        </ThreadPrimitive.Root>
      </AssistantRuntimeProvider>
    </AgUiActivityProvider>
  )
}

afterEach(() => {
  vi.mocked(apiFetch).mockReset()
})

describe('mounted widget authorization', () => {
  it('ignores a pending private response after switching threads', async () => {
    const pending = Promise.withResolvers<unknown>()
    vi.mocked(apiFetch).mockImplementation((path) =>
      path.includes(threadA) ? pending.promise : Promise.reject(new Error('not found')),
    )
    const view = render(<Harness conversationId={threadA} />)
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls).toHaveLength(1))
    const [firstPath, firstOptions] = vi.mocked(apiFetch).mock.calls[0] ?? []
    expect(firstPath).toBe(
      `/api/conversations/${threadA}/runs/${runId}/mcp-apps/call%2Fmaps%3A1?binding_id=${bindingId}`,
    )
    expect(firstOptions).toEqual(expect.objectContaining({ method: 'GET', cache: 'no-store' }))

    view.rerender(<Harness conversationId={threadB} />)
    await waitFor(() => expect(vi.mocked(apiFetch).mock.calls).toHaveLength(2))
    expect(firstOptions?.signal?.aborted).toBe(true)
    await act(async () => {
      pending.resolve(source)
      await pending.promise
    })
    expect(view.getByTestId('standard-tool')).toBeInTheDocument()
    expect(vi.mocked(apiFetch).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(
      false,
    )
  })

  it('drops an existing frame when returning to the page with revoked access', async () => {
    let allowed = true
    vi.mocked(apiFetch).mockImplementation(async (_path, options) => {
      if (options?.method === 'POST')
        return { uri, html: '<main>Map</main>', mimeType: 'text/html;profile=mcp-app' }
      if (allowed) return source
      throw new Error('not found')
    })
    const view = render(<Harness conversationId={threadA} />)
    await waitFor(() =>
      expect(view.container.querySelector('[data-mcp-app-resource]')).toHaveAttribute(
        'data-mcp-app-resource',
        uri,
      ),
    )
    expect(apiFetch).toHaveBeenCalledWith(
      `/api/conversations/${threadA}/runs/${runId}/mcp-apps/call%2Fmaps%3A1`,
      expect.objectContaining({ method: 'POST' }),
    )
    allowed = false
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await waitFor(() => expect(view.getByTestId('standard-tool')).toBeInTheDocument())
    expect(view.container.querySelector('[data-mcp-app-resource]')).toBeNull()
    expect(
      vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'GET'),
    ).toHaveLength(2)
  })
})
