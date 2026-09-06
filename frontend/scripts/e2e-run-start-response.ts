export type RunStartCommandResponse = {
  readonly ok: boolean
  readonly requestConversationId: string
  readonly runIdHeader: string | null
  readonly body: unknown
}

export type AcceptedRunStart = {
  readonly conversationId: string
  readonly runId: string
}

export const MAX_RUN_START_RESPONSE_BYTES = 8_192

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function commandMethodFromRequest(
  method: string,
  url: string,
  raw: string | null,
): string | null {
  if (method !== 'POST' || !url.includes('/langgraph/threads/')) return null
  if (!url.endsWith('/commands') || !raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    return isRecord(parsed) && typeof parsed.method === 'string' ? parsed.method : null
  } catch {
    return null
  }
}

/** Parses only the exact same-conversation LangGraph command endpoint on this API origin. */
export function parseConversationRunStartUrl(responseUrl: string, apiBase: string): string | null {
  try {
    const response = new URL(responseUrl)
    const api = new URL(apiBase)
    if (response.origin !== api.origin || response.search || response.hash) return null

    const segments = response.pathname.split('/')
    if (
      segments.length !== 8 ||
      segments[0] !== '' ||
      segments[1] !== 'api' ||
      segments[2] !== 'conversations' ||
      segments[4] !== 'langgraph' ||
      segments[5] !== 'threads' ||
      segments[7] !== 'commands'
    ) {
      return null
    }

    const conversationSegment = segments[3]
    const threadSegment = segments[6]
    if (!conversationSegment || conversationSegment !== threadSegment) return null

    const conversationId = decodeURIComponent(conversationSegment)
    return conversationId.trim() && !conversationId.includes('/') ? conversationId : null
  } catch {
    return null
  }
}

export function parseRunStartResponseBody(body: Uint8Array): unknown {
  if (body.byteLength > MAX_RUN_START_RESPONSE_BYTES) {
    throw new Error('run.start command response exceeded the supported size')
  }
  try {
    return JSON.parse(new TextDecoder().decode(body))
  } catch {
    throw new Error('run.start command did not return JSON')
  }
}

function acceptedResultId(result: Record<string, unknown>, key: string): string {
  const value = result[key]
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`run.start command did not include result.${key}`)
  }
  return value
}

/** Parses the non-secret protocol contract for an accepted run.start response. */
export function acceptedRunStart(response: RunStartCommandResponse): AcceptedRunStart {
  if (!response.ok) throw new Error('run.start command did not succeed')
  if (!isRecord(response.body) || response.body.type !== 'success') {
    throw new Error('run.start command did not return an accepted success response')
  }
  const result = response.body.result
  if (!isRecord(result) || result.status !== 'accepted') {
    throw new Error('run.start command did not return an accepted success response')
  }
  const conversationId = acceptedResultId(result, 'conversation_id')
  const threadId = acceptedResultId(result, 'thread_id')
  const runId = acceptedResultId(result, 'run_id')
  if (conversationId !== threadId) {
    throw new Error('run.start command returned inconsistent conversation and thread ids')
  }
  if (response.requestConversationId !== conversationId) {
    throw new Error('run.start command returned an inconsistent request conversation id')
  }
  if (!response.runIdHeader?.trim()) {
    throw new Error('run.start command did not include X-Run-Id')
  }
  if (response.runIdHeader !== runId) {
    throw new Error('run.start command returned inconsistent run ids')
  }
  return { conversationId, runId }
}
