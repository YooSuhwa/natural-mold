import { act, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AssistantRuntimeProvider,
  AuiConfig,
  MessagePrimitive,
  ThreadPrimitive,
  Tools,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { render, waitFor } from '../../../../../tests/test-utils'
import { createMoldyMcpAppRenderer } from '../renderer'
import { apiFetch } from '@/lib/api/client'

const onNewMessage = vi.hoisted(() => vi.fn(async () => {}))

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/client')>()
  return { ...actual, apiFetch: vi.fn() }
})

const RUN_ID = '4dc6b0a0-8c0c-4eca-86be-758ba73a8a54'
const APP_RESOURCE_RESPONSE = {
  uri: 'ui://maps/route.html',
  mimeType: 'text/html;profile=mcp-app',
  html: '<html><body>route widget</body></html>',
} as const

async function loadAppFrame(container: HTMLElement): Promise<HTMLIFrameElement> {
  const iframe = await waitFor(() => {
    const mountedFrame = container.querySelector('iframe')
    if (!(mountedFrame instanceof HTMLIFrameElement)) {
      throw new Error('expected mounted MCP App frame')
    }
    return mountedFrame
  })
  await act(async () => {
    fireEvent.load(iframe)
  })
  return iframe
}

async function initializeAppFrame(container: HTMLElement) {
  const iframe = await loadAppFrame(container)
  const frameWindow = iframe.contentWindow
  if (!frameWindow) throw new Error('expected mounted MCP App frame')
  const origin = new URL(iframe.src).origin
  const postMessage = vi.spyOn(frameWindow, 'postMessage')
  window.dispatchEvent(
    new MessageEvent('message', {
      origin,
      source: frameWindow,
      data: { jsonrpc: '2.0', id: 'initialize', method: 'ui/initialize', params: {} },
    }),
  )
  await waitFor(() =>
    expect(postMessage.mock.calls.map(([message]) => message)).toContainEqual(
      expect.objectContaining({ id: 'initialize', result: expect.any(Object) }),
    ),
  )
  return { frameWindow, origin, postMessage }
}

function Harness() {
  const runtime = useExternalStoreRuntime({
    messages: [{ id: 'assistant-1' }],
    isRunning: false,
    onNew: onNewMessage,
    convertMessage: (): ThreadMessageLike => ({
      id: 'assistant-1',
      role: 'assistant',
      content: [
        {
          type: 'tool-call',
          toolCallId: 'call-route-1',
          toolName: 'render_route',
          args: { destination: 'Sejong' },
          argsText: '{"destination":"Sejong"}',
          result: 'Route ready',
          mcp: {
            app: {
              resourceUri: 'ui://maps/route.html',
              mimeType: 'text/html;profile=mcp-app',
              serverId: `moldy-mcp-app-v1:${RUN_ID}:call-route-1`,
            },
          },
        },
      ],
    }),
  })
  const config = AuiConfig({
    tools: Tools({
      mcpApp: createMoldyMcpAppRenderer({
        conversationId: 'conversation-1',
        fallback: <div data-testid="standard-fallback" />,
      }),
    }),
  })
  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages
            components={{
              AssistantMessage: () => (
                <MessagePrimitive.Root>
                  <MessagePrimitive.Parts>
                    {({ part }) => (part.type === 'tool-call' ? <>{part.toolUI}</> : null)}
                  </MessagePrimitive.Parts>
                </MessagePrimitive.Root>
              ),
              UserMessage: () => null,
            }}
          />
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

describe('Moldy MCP App renderer integration', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset()
    onNewMessage.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads and mounts a valid resource through the installed renderer and scoped endpoint', async () => {
    // Given a valid backend-owned MCP App tool part and resource response.
    vi.mocked(apiFetch).mockResolvedValue({
      uri: 'ui://maps/route.html',
      mimeType: 'text/html;profile=mcp-app',
      html: '<html><body>route widget</body></html>',
      meta: { prefersBorder: true },
    })

    // When the actual assistant-ui Tools resource renders the part.
    const view = render(<Harness />)

    // Then the official renderer mounts its sandbox host and uses the immutable proxy path.
    await waitFor(() =>
      expect(view.container.querySelector('[data-mcp-app-resource]')).toHaveAttribute(
        'data-mcp-app-resource',
        'ui://maps/route.html',
      ),
    )
    expect(apiFetch).toHaveBeenCalledWith(
      `/api/conversations/conversation-1/runs/${RUN_ID}/mcp-apps/call-route-1`,
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it.each([403, 409])(
    'keeps the standard fallback when authority rejects with %s',
    async (status) => {
      // Given the backend rejects a revoked or stale invocation binding.
      vi.mocked(apiFetch).mockRejectedValue(
        Object.assign(new Error('MCP App unavailable'), { status }),
      )

      // When the official renderer tries to load the bound resource.
      const view = render(<Harness />)
      await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1))

      // Then the ordinary tool UI remains visible and no untrusted document is mounted.
      expect(view.getByTestId('standard-fallback')).toBeInTheDocument()
      expect(view.container.querySelector('[data-mcp-app-resource]')).toBeNull()
    },
  )

  it('uses the standard fallback for a malformed resource response', async () => {
    // Given a host response that does not contain renderable HTML.
    vi.mocked(apiFetch).mockResolvedValue({
      uri: 'ui://maps/route.html',
      mimeType: 'text/html;profile=mcp-app',
      html: '',
    })

    // When the official RemoteHost validates it.
    const view = render(<Harness />)
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1))

    // Then the app cannot mount and the ordinary tool fallback remains.
    expect(view.getByTestId('standard-fallback')).toBeInTheDocument()
    expect(view.container.querySelector('[data-mcp-app-resource]')).toBeNull()
  })

  it('denies browser actions after the app completes the real bridge handshake', async () => {
    // Given a loaded app frame whose initialized bridge has answered the widget.
    vi.mocked(apiFetch).mockResolvedValue(APP_RESOURCE_RESPONSE)
    const view = render(<Harness />)
    const { frameWindow, origin, postMessage } = await initializeAppFrame(view.container)
    expect(postMessage.mock.calls.map(([message]) => message)).toContainEqual(
      expect.objectContaining({
        id: 'initialize',
        result: expect.objectContaining({
          capabilities: expect.objectContaining({
            ui: expect.objectContaining({ openLink: true, sendMessage: true }),
          }),
        }),
      }),
    )

    // When the initialized widget requests the two browser actions denied by Moldy.
    const openWindow = vi.spyOn(window, 'open')
    for (const request of [
      { id: 'open-link', method: 'ui/open-link', params: { url: 'https://attacker.invalid' } },
      { id: 'send-message', method: 'ui/message', params: { text: 'run another action' } },
    ]) {
      window.dispatchEvent(
        new MessageEvent('message', {
          origin,
          source: frameWindow,
          data: { jsonrpc: '2.0', ...request },
        }),
      )
    }

    // Then both calls return policy errors without opening a URL or appending a message.
    await waitFor(() => {
      for (const id of ['open-link', 'send-message']) {
        expect(postMessage.mock.calls.map(([message]) => message)).toContainEqual(
          expect.objectContaining({
            id,
            error: expect.objectContaining({ code: -32603 }),
          }),
        )
      }
    })
    expect(openWindow).not.toHaveBeenCalled()
    expect(onNewMessage).not.toHaveBeenCalled()
    openWindow.mockRestore()
  })

  it('rejects a forged postMessage after a legitimate bridge call succeeds', async () => {
    // Given an initialized app frame with one proven legitimate data-plane call.
    vi.mocked(apiFetch).mockResolvedValue(APP_RESOURCE_RESPONSE)
    const view = render(<Harness />)
    const { frameWindow, origin } = await initializeAppFrame(view.container)
    window.dispatchEvent(
      new MessageEvent('message', {
        origin,
        source: frameWindow,
        data: {
          jsonrpc: '2.0',
          id: 'legitimate-call',
          method: 'tools/call',
          params: { name: 'refresh_route', arguments: {} },
        },
      }),
    )
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
    expect(apiFetch).toHaveBeenLastCalledWith(
      `/api/conversations/conversation-1/runs/${RUN_ID}/mcp-apps/call-route-1`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          method: 'tools/call',
          params: { name: 'refresh_route', arguments: {} },
        }),
      }),
    )

    // When an attacker forges the same bridge method from the top-level window.
    window.dispatchEvent(
      new MessageEvent('message', {
        origin: 'https://attacker.invalid',
        source: window,
        data: {
          jsonrpc: '2.0',
          id: 'forged-call',
          method: 'tools/call',
          params: { name: 'forged_tool', arguments: {} },
        },
      }),
    )

    // Then source/origin validation prevents any additional host request.
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))
  })
})
