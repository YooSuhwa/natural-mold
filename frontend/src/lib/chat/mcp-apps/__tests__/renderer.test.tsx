import { describe, expect, it, vi } from 'vitest'
import { McpAppRenderer, McpAppsRemoteHost } from '@assistant-ui/react'
import { createMoldyMcpAppRenderer } from '../renderer'

vi.mock('@assistant-ui/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@assistant-ui/react')>()
  return {
    ...actual,
    McpAppRenderer: vi.fn((options) => ({ hook: vi.fn(), args: [options] })),
    McpAppsRemoteHost: vi.fn((options) => ({ hook: vi.fn(), args: [options] })),
  }
})

describe('Moldy MCP App renderer resource', () => {
  it('uses the installed official renderer with a thread-wide scoped RemoteHost', () => {
    // Given the current conversation and its existing standard tool fallback.
    const fallback = <div data-testid="standard-tool-fallback" />

    // When Moldy creates the MCP App rendering resource.
    createMoldyMcpAppRenderer({ conversationId: 'conversation-1', fallback })

    // Then it composes the official 0.15 renderer and host without a duplicate runtime.
    expect(McpAppsRemoteHost).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/mcp-apps/scoped/conversation-1',
        fetch: expect.any(Function),
      }),
    )
    expect(McpAppRenderer).toHaveBeenCalledWith(
      expect.objectContaining({
        fallback,
        loadingFallback: fallback,
        errorFallback: fallback,
        host: expect.objectContaining({ hook: expect.any(Function) }),
        forPart: expect.any(Function),
      }),
    )
  })

  it('denies generic browser actions for each part while leaving data calls server-bound', async () => {
    // Given a renderer whose part policy is captured by the official resource.
    createMoldyMcpAppRenderer({ conversationId: 'conversation-1', fallback: null })
    const options = vi.mocked(McpAppRenderer).mock.calls.at(-1)?.[0]
    if (!options?.forPart) throw new Error('expected per-part MCP App policy')

    // When the widget asks for browser-side actions not granted by Moldy.
    const policy = options.forPart({
      type: 'tool-call',
      toolCallId: 'call-1',
      toolName: 'render_route',
      args: {},
      argsText: '{}',
    })
    expect(policy).not.toHaveProperty('host')
    expect(policy.handlers).not.toHaveProperty('callTool')
    expect(policy.handlers).not.toHaveProperty('readResource')

    // Then both generic actions fail closed and no arbitrary link or message executes.
    await expect(policy.handlers?.openLink?.({ url: 'https://attacker.invalid' })).rejects.toThrow(
      'MCP App capability is not granted',
    )
    await expect(policy.handlers?.sendMessage?.({ text: 'run another action' })).rejects.toThrow(
      'MCP App capability is not granted',
    )
  })
})
