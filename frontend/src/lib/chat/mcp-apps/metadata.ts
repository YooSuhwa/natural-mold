import type { ThreadMessage } from '@assistant-ui/react'

const APP_ROUTE_PREFIX = 'moldy-mcp-app-v1:'
const MAX_ROUTE_TOKEN_LENGTH = 1_024
const MAX_TOOL_CALL_ID_LENGTH = 255
const MAX_RESOURCE_URI_LENGTH = 2_048
const MAX_PROJECTED_METADATA_LENGTH = 64 * 1_024
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export interface McpAppRouteContext {
  readonly runId: string
  readonly toolCallId: string
}

interface ProjectedMcpApp {
  readonly resourceUri: string
  readonly mimeType: 'text/html;profile=mcp-app'
  readonly visibility?: readonly ('model' | 'app')[]
  readonly serverId: string
}

interface ProjectedMcpAppPart {
  readonly app: ProjectedMcpApp
  readonly output: Record<string, unknown>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readVisibility(value: unknown): readonly ('model' | 'app')[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > 2) return undefined
  const visibility = value.filter(
    (item): item is 'model' | 'app' => item === 'model' || item === 'app',
  )
  return visibility.length === value.length ? visibility : undefined
}

function createMcpAppRouteToken(context: McpAppRouteContext): string {
  return `${APP_ROUTE_PREFIX}${context.runId}:${encodeURIComponent(context.toolCallId)}`
}

function boundedRecord(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null
  try {
    const serialized = JSON.stringify(value)
    return typeof serialized === 'string' &&
      new TextEncoder().encode(serialized).byteLength <= MAX_PROJECTED_METADATA_LENGTH
      ? value
      : null
  } catch {
    return null
  }
}

export function readMcpAppRouteToken(value: unknown): McpAppRouteContext | null {
  if (
    typeof value !== 'string' ||
    value.length > MAX_ROUTE_TOKEN_LENGTH ||
    !value.startsWith(APP_ROUTE_PREFIX)
  ) {
    return null
  }
  const route = value.slice(APP_ROUTE_PREFIX.length)
  const separator = route.indexOf(':')
  if (separator !== 36) return null
  const runId = route.slice(0, separator)
  if (!UUID_PATTERN.test(runId)) return null
  try {
    const toolCallId = decodeURIComponent(route.slice(separator + 1))
    if (!toolCallId || toolCallId.length > MAX_TOOL_CALL_ID_LENGTH) return null
    return { runId, toolCallId }
  } catch (error: unknown) {
    if (error instanceof URIError) return null
    throw error
  }
}

function projectMcpApp(
  artifact: unknown,
  toolCallId: string,
  content: unknown,
): ProjectedMcpAppPart | null {
  if (!isRecord(artifact) || !isRecord(artifact.mcp_app)) return null
  const app = artifact.mcp_app
  if (app.version !== 1) return null
  const bindingId = app.binding_id
  const runId = app.run_id
  const resourceUri = app.resource_uri
  if (
    typeof bindingId !== 'string' ||
    !UUID_PATTERN.test(bindingId) ||
    typeof runId !== 'string' ||
    !UUID_PATTERN.test(runId) ||
    typeof resourceUri !== 'string' ||
    !resourceUri.startsWith('ui://') ||
    resourceUri.length > MAX_RESOURCE_URI_LENGTH ||
    !isRecord(app.tool_meta) ||
    !isRecord(app.tool_meta.ui) ||
    app.tool_meta.ui.resourceUri !== resourceUri
  ) {
    return null
  }
  const visibility = readVisibility(app.tool_meta.ui.visibility)
  const structuredContent = boundedRecord(artifact.structured_content)
  const resultMeta = boundedRecord(app.result_meta)
  return {
    app: {
      resourceUri,
      mimeType: 'text/html;profile=mcp-app',
      ...(visibility ? { visibility } : {}),
      serverId: createMcpAppRouteToken({ runId, toolCallId }),
    },
    output: {
      content,
      ...(structuredContent ? { structuredContent } : {}),
      ...(resultMeta ? { _meta: resultMeta } : {}),
    },
  }
}

/** Adds only the assistant-ui metadata consumed by the installed MCP Apps renderer. */
export function attachMcpAppsMetadata(
  messages: readonly ThreadMessage[],
): readonly ThreadMessage[] {
  return messages.map((message) => {
    if (message.role !== 'assistant') return message
    let changed = false
    const content = message.content.map((part) => {
      if (typeof part === 'string' || part.type !== 'tool-call') return part
      const projected = projectMcpApp(part.artifact, part.toolCallId, part.result)
      if (!projected) return part
      changed = true
      return { ...part, result: projected.output, mcp: { app: projected.app } }
    })
    return changed ? { ...message, content } : message
  })
}
