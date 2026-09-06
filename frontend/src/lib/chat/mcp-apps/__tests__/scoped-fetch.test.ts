import { describe, expect, it, vi } from 'vitest'
import { createMcpAppsScopedFetch } from '../scoped-fetch'

const RUN_ID = '4dc6b0a0-8c0c-4eca-86be-758ba73a8a54'
const ROUTE_TOKEN = `moldy-mcp-app-v1:${RUN_ID}:call-route-1`

describe('MCP Apps scoped RemoteHost fetch', () => {
  it('routes a widget tool call through the immutable invocation endpoint', async () => {
    // Given a host request whose local token was bound to one rendered tool part.
    const invoke = vi.fn().mockResolvedValue({ structuredContent: { ok: true } })
    const scopedFetch = createMcpAppsScopedFetch('conversation-1', invoke)

    // When assistant-ui RemoteHost calls the scoped fetch adapter.
    const response = await scopedFetch('/ignored-thread-wide-host', {
      method: 'POST',
      body: JSON.stringify({
        method: 'tools/call',
        params: { name: 'zoom_route', arguments: { level: 2 }, serverId: ROUTE_TOKEN },
      }),
    })

    // Then only the persisted provenance endpoint is invoked and the local token is stripped.
    expect(invoke).toHaveBeenCalledWith(
      `/api/conversations/conversation-1/runs/${RUN_ID}/mcp-apps/call-route-1`,
      {
        method: 'POST',
        body: JSON.stringify({
          method: 'tools/call',
          params: { name: 'zoom_route', arguments: { level: 2 } },
        }),
      },
    )
    expect(await response.json()).toEqual({ structuredContent: { ok: true } })
  })

  it('rejects a forged routing token without touching the backend', async () => {
    // Given a forged widget request with no renderer-issued token.
    const invoke = vi.fn()
    const scopedFetch = createMcpAppsScopedFetch('conversation-1', invoke)

    // When the request crosses the browser host boundary.
    const request = scopedFetch('/ignored', {
      method: 'POST',
      body: JSON.stringify({
        method: 'resources/read',
        params: { uri: 'ui://maps/route.html', serverId: 'server-chosen-by-widget' },
      }),
    })

    // Then it is denied locally and cannot select a backend server or tool call.
    await expect(request).rejects.toThrow('Invalid MCP App invocation context')
    expect(invoke).not.toHaveBeenCalled()
  })

  it.each([
    ['stale', Object.assign(new Error('MCP App context is stale'), { status: 409 })],
    ['denied', Object.assign(new Error('MCP App tool is forbidden'), { status: 403 })],
  ])('propagates a backend %s decision to the renderer fallback', async (_label, error) => {
    // Given the authority endpoint rejects a previously rendered context.
    const invoke = vi.fn().mockRejectedValue(error)
    const scopedFetch = createMcpAppsScopedFetch('conversation-1', invoke)

    // When a data-plane request is attempted.
    const request = scopedFetch('/ignored', {
      method: 'POST',
      body: JSON.stringify({
        method: 'resources/list',
        params: { serverId: ROUTE_TOKEN },
      }),
    })

    // Then the exact authority failure rejects; no permissive response is fabricated.
    await expect(request).rejects.toBe(error)
    expect(invoke).toHaveBeenCalledTimes(1)
  })

  it('injects a restrictive CSP ahead of a valid MCP App document', async () => {
    // Given a bound resource response with a narrowly declared connect origin.
    const invoke = vi.fn().mockResolvedValue({
      uri: 'ui://maps/route.html',
      mimeType: 'text/html;profile=mcp-app',
      html: '<html><head></head><body>route</body></html>',
      meta: {
        csp: {
          connectDomains: [
            'https://tiles.example.com',
            'http://insecure.example.com',
            'https://user:password@credential.example.com',
          ],
        },
      },
    })
    const scopedFetch = createMcpAppsScopedFetch('conversation-1', invoke)

    // When the official RemoteHost loads the resource through the adapter.
    const response = await scopedFetch('/ignored', {
      method: 'POST',
      body: JSON.stringify({
        method: 'mcp-apps/read-resource',
        params: { uri: 'ui://maps/route.html', serverId: ROUTE_TOKEN },
      }),
    })

    // Then a deny-by-default policy precedes all widget markup.
    const payload = await response.json()
    expect(payload.html).toMatch(/^<meta http-equiv="Content-Security-Policy"/)
    expect(payload.html).toContain("default-src 'none'")
    expect(payload.html).toContain('connect-src https://tiles.example.com')
    expect(payload.html).not.toContain('insecure.example.com')
    expect(payload.html).not.toContain('credential.example.com')
  })
})
