import type { ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'
import { attachMcpAppsMetadata, readMcpAppRouteToken } from '../metadata'

const RUN_ID = '4dc6b0a0-8c0c-4eca-86be-758ba73a8a54'
const BINDING_ID = '65cfc3bf-c4ff-4500-a5e8-4ab695989d50'

function assistantMessage(artifact: unknown): ThreadMessage {
  return {
    id: 'assistant-1',
    createdAt: new Date(0),
    role: 'assistant',
    status: { type: 'complete', reason: 'stop' },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
    content: [
      {
        type: 'tool-call',
        toolCallId: 'call/route:1',
        toolName: 'render_route',
        args: { destination: 'Sejong' },
        argsText: '{"destination":"Sejong"}',
        result: 'Route ready',
        artifact,
      },
    ],
  }
}

function validMcpApp(): Record<string, unknown> {
  return {
    version: 1,
    binding_id: BINDING_ID,
    run_id: RUN_ID,
    resource_uri: 'ui://maps/route.html',
    tool_meta: {
      ui: {
        resourceUri: 'ui://maps/route.html',
        visibility: ['model', 'app'],
      },
    },
    result_meta: { initialView: 'overview' },
  }
}

function validArtifact(): Record<string, unknown> {
  return {
    mcp_app: validMcpApp(),
    structured_content: { distanceKm: 11.2 },
  }
}

describe('MCP App metadata projection', () => {
  it('attaches only standard renderer metadata and binds it to the real tool call', () => {
    // Given a joined assistant tool part carrying the backend-owned artifact.
    const messages = [assistantMessage(validArtifact())]

    // When the joined messages are projected for assistant-ui.
    const projected = attachMcpAppsMetadata(messages)

    // Then the renderer metadata carries a local route token, not backend authority.
    const part = projected[0]?.content[0]
    expect(part?.type).toBe('tool-call')
    if (typeof part === 'string' || part?.type !== 'tool-call') {
      throw new Error('expected tool-call part')
    }
    expect(part.mcp).toEqual({
      app: {
        resourceUri: 'ui://maps/route.html',
        mimeType: 'text/html;profile=mcp-app',
        visibility: ['model', 'app'],
        serverId: expect.stringMatching(/^moldy-mcp-app-v1:/),
      },
    })
    expect(readMcpAppRouteToken(part.mcp?.app?.serverId)).toEqual({
      runId: RUN_ID,
      toolCallId: 'call/route:1',
    })
    expect(part.result).toEqual({
      content: 'Route ready',
      structuredContent: { distanceKm: 11.2 },
      _meta: { initialView: 'overview' },
    })
    expect(JSON.stringify(part.mcp)).not.toContain(BINDING_ID)
    expect(JSON.stringify(part.mcp)).not.toContain('initialView')
  })

  it('omits oversized optional output metadata while preserving the app and tool content', () => {
    // Given an old or tampered history row exceeds the bounded frontend projection.
    const artifact = validArtifact()
    artifact.structured_content = { value: 'x'.repeat(70_000) }
    const messages = [assistantMessage(artifact)]

    // When it is projected into a standard MCP CallToolResult.
    const projected = attachMcpAppsMetadata(messages)

    // Then only the existing content and separately validated app binding survive.
    const part = projected[0]?.content[0]
    if (typeof part === 'string' || part?.type !== 'tool-call') {
      throw new Error('expected tool-call part')
    }
    expect(part.mcp?.app?.resourceUri).toBe('ui://maps/route.html')
    expect(part.result).toEqual({
      content: 'Route ready',
      _meta: { initialView: 'overview' },
    })
  })

  it.each([
    ['missing run', { ...validArtifact(), mcp_app: { version: 1 } }],
    ['resource mismatch', { mcp_app: { ...validMcpApp(), resource_uri: 'ui://maps/forged.html' } }],
    [
      'unsupported URI',
      {
        mcp_app: {
          ...validMcpApp(),
          resource_uri: 'https://attacker.invalid/widget.html',
          tool_meta: { ui: { resourceUri: 'https://attacker.invalid/widget.html' } },
        },
      },
    ],
  ])('leaves the standard tool fallback active for malformed %s metadata', (_label, artifact) => {
    // Given malformed or unsupported producer metadata.
    const messages = [assistantMessage(artifact)]

    // When projection runs.
    const projected = attachMcpAppsMetadata(messages)

    // Then no MCP App renderer marker is attached.
    const part = projected[0]?.content[0]
    expect(part?.type).toBe('tool-call')
    if (typeof part === 'string' || part?.type !== 'tool-call') {
      throw new Error('expected tool-call part')
    }
    expect(part.mcp).toBeUndefined()
  })

  it('rejects forged and oversized route tokens before any host request', () => {
    // Given attacker-controlled and oversized routing strings.
    const forged = `moldy-mcp-app-v1:${RUN_ID}:${'%2F'.repeat(300)}`

    // When the host parses them.
    const wrongVersion = readMcpAppRouteToken(`moldy-mcp-app-v2:${RUN_ID}:call-1`)
    const oversized = readMcpAppRouteToken(forged)

    // Then neither becomes an invocation route.
    expect(wrongVersion).toBeNull()
    expect(oversized).toBeNull()
  })
})
