export type RunStartCommandResponse = {
  readonly ok: boolean
  readonly runIdHeader: string | null
  readonly body: unknown
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

export function isConversationRunStartUrl(
  responseUrl: string,
  apiBase: string,
  conversationId: string,
): boolean {
  try {
    const response = new URL(responseUrl)
    const api = new URL(apiBase)
    const encodedConversationId = encodeURIComponent(conversationId)
    const expectedPath = `/api/conversations/${encodedConversationId}/langgraph/threads/${encodedConversationId}/commands`
    return response.origin === api.origin && response.pathname === expectedPath
  } catch {
    return false
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

/** Validates the non-secret protocol contract for an accepted run.start response. */
export function acceptedRunId(response: RunStartCommandResponse): string {
  if (!response.ok) throw new Error('run.start command did not succeed')
  if (!isRecord(response.body) || response.body.type !== 'success') {
    throw new Error('run.start command did not return an accepted success response')
  }
  const result = response.body.result
  if (!isRecord(result) || result.status !== 'accepted') {
    throw new Error('run.start command did not return an accepted success response')
  }
  const bodyRunId = result.run_id
  if (typeof bodyRunId !== 'string' || !bodyRunId.trim()) {
    throw new Error('run.start command did not include result.run_id')
  }
  if (!response.runIdHeader?.trim()) {
    throw new Error('run.start command did not include X-Run-Id')
  }
  if (response.runIdHeader !== bodyRunId) {
    throw new Error('run.start command returned inconsistent run ids')
  }
  return bodyRunId
}
