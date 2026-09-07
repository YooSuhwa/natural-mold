'use client'

import type { ReactNode } from 'react'
import { McpAppRenderer, McpAppsRemoteHost, useAuiState } from '@assistant-ui/react'
import { GenericToolFallback } from '@/components/chat/tool-ui/generic-tool-ui'
import { createMcpAppsScopedFetch } from './scoped-fetch'

export interface MoldyMcpAppRendererOptions {
  readonly conversationId: string
  readonly fallback?: ReactNode
}

async function denyBrowserCapability(): Promise<never> {
  throw new Error('MCP App capability is not granted')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function McpAppStandardToolFallback() {
  const part = useAuiState((state) => state.part)
  if (part.type !== 'tool-call') return null
  return (
    <GenericToolFallback
      toolCallId={part.toolCallId}
      toolName={part.toolName}
      args={isRecord(part.args) ? part.args : {}}
      result={part.result}
      status={part.status}
    />
  )
}

/** One thread-wide renderer whose RemoteHost dispatches by renderer-issued part tokens. */
export function createMoldyMcpAppRenderer({
  conversationId,
  fallback,
}: MoldyMcpAppRendererOptions) {
  const resolvedFallback = fallback ?? <McpAppStandardToolFallback />
  return McpAppRenderer({
    host: McpAppsRemoteHost({
      // The URL is an identity key for the official resource; the scoped fetch
      // performs the real request. Including the conversation prevents a
      // same-URL host instance from retaining another thread's fetch closure.
      url: `/api/mcp-apps/scoped/${encodeURIComponent(conversationId)}`,
      fetch: createMcpAppsScopedFetch(conversationId),
    }),
    hostInfo: { name: 'Moldy', version: '1' },
    hostContext: { displayMode: 'inline', availableDisplayModes: ['inline'] },
    sandbox: { sandbox: [], product: 'moldy-mcp-app' },
    maxHeight: 800,
    fallback: resolvedFallback,
    loadingFallback: resolvedFallback,
    errorFallback: resolvedFallback,
    forPart: () => ({
      handlers: {
        openLink: denyBrowserCapability,
        sendMessage: denyBrowserCapability,
      },
    }),
  })
}
