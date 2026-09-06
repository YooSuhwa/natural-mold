import { apiFetch } from '@/lib/api/client'
import { readMcpAppRouteToken } from './metadata'

const ALLOWED_METHODS = new Set([
  'mcp-apps/read-resource',
  'tools/call',
  'resources/read',
  'resources/list',
])
const MAX_REQUEST_BODY_LENGTH = 128 * 1_024
const MAX_CSP_DOMAINS = 16

type InvokeApi = (path: string, options: RequestInit) => Promise<unknown>

export class McpAppHostRequestError extends Error {
  readonly code: 'invalid_context' | 'invalid_request'

  constructor(code: 'invalid_context' | 'invalid_request', message: string) {
    super(message)
    this.name = 'McpAppHostRequestError'
    this.code = code
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeOrigin(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' &&
      url.username === '' &&
      url.password === '' &&
      url.search === '' &&
      url.hash === '' &&
      (url.pathname === '/' || url.pathname === '')
      ? url.origin
      : null
  } catch (error: unknown) {
    if (error instanceof TypeError) return null
    throw error
  }
}

function origins(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return []
  const unique = new Set<string>()
  for (const item of value.slice(0, MAX_CSP_DOMAINS)) {
    const origin = safeOrigin(item)
    if (origin) unique.add(origin)
  }
  return [...unique]
}

function cspFor(meta: unknown): string {
  const csp = isRecord(meta) && isRecord(meta.csp) ? meta.csp : {}
  const connect = origins(csp.connectDomains)
  const resources = origins(csp.resourceDomains)
  const frames = origins(csp.frameDomains)
  const optionalSources = (values: readonly string[]): string =>
    values.length > 0 ? ` ${values.join(' ')}` : ''
  const exclusiveSources = (values: readonly string[]): string =>
    values.length > 0 ? values.join(' ') : "'none'"
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline'${optionalSources(resources)}`,
    `style-src 'unsafe-inline'${optionalSources(resources)}`,
    `img-src data: blob:${optionalSources(resources)}`,
    `font-src data:${optionalSources(resources)}`,
    `media-src data: blob:${optionalSources(resources)}`,
    `connect-src ${exclusiveSources(connect)}`,
    `frame-src ${exclusiveSources(frames)}`,
    "form-action 'none'",
    "base-uri 'none'",
  ].join('; ')
}

function hardenResource(value: unknown): unknown {
  if (!isRecord(value) || typeof value.html !== 'string' || value.html.trim() === '') return value
  const policy = cspFor(value.meta)
  const csp = `<meta http-equiv="Content-Security-Policy" content="${policy}">`
  return { ...value, html: `${csp}${value.html}` }
}

function parseRequest(init?: RequestInit): {
  readonly method: string
  readonly params: Record<string, unknown>
  readonly context: NonNullable<ReturnType<typeof readMcpAppRouteToken>>
} {
  if (init?.method?.toUpperCase() !== 'POST' || typeof init.body !== 'string') {
    throw new McpAppHostRequestError('invalid_request', 'Invalid MCP App host request')
  }
  if (init.body.length > MAX_REQUEST_BODY_LENGTH) {
    throw new McpAppHostRequestError('invalid_request', 'MCP App host request is too large')
  }
  let decoded: unknown
  try {
    decoded = JSON.parse(init.body)
  } catch (error: unknown) {
    if (error instanceof SyntaxError) {
      throw new McpAppHostRequestError('invalid_request', 'Invalid MCP App host request')
    }
    throw error
  }
  if (
    !isRecord(decoded) ||
    typeof decoded.method !== 'string' ||
    !ALLOWED_METHODS.has(decoded.method)
  ) {
    throw new McpAppHostRequestError('invalid_request', 'Unsupported MCP App host method')
  }
  const params = isRecord(decoded.params) ? decoded.params : {}
  const context = readMcpAppRouteToken(params.serverId)
  if (!context) {
    throw new McpAppHostRequestError('invalid_context', 'Invalid MCP App invocation context')
  }
  return { method: decoded.method, params, context }
}

export function createMcpAppsScopedFetch(
  conversationId: string,
  invoke: InvokeApi = (path, options) => apiFetch(path, options),
): typeof fetch {
  return async (_input, init) => {
    const request = parseRequest(init)
    const backendParams = Object.fromEntries(
      Object.entries(request.params).filter(([key]) => key !== 'serverId'),
    )
    const path =
      `/api/conversations/${encodeURIComponent(conversationId)}` +
      `/runs/${encodeURIComponent(request.context.runId)}` +
      `/mcp-apps/${encodeURIComponent(request.context.toolCallId)}`
    const result = await invoke(path, {
      method: 'POST',
      body: JSON.stringify({ method: request.method, params: backendParams }),
    })
    const payload = request.method === 'mcp-apps/read-resource' ? hardenResource(result) : result
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  }
}
