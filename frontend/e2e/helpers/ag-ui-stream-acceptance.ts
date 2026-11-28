import type { Request, Response } from '@playwright/test'
import { z } from 'zod'

import { readAcceptance, type MoldyAgUiAcceptance } from '../../src/lib/ag-ui/moldy-agent-receipt'

const commandIdentity = z.object({
  protocolVersion: z.literal('1.0'),
  threadId: z.string(),
  runId: z.string(),
})

export function agUiCommandThread(
  request: Pick<Request, 'url' | 'method'>,
  apiBase: string,
): string | null {
  if (request.method() !== 'POST') return null
  try {
    const url = new URL(request.url())
    if (
      url.origin !== new URL(apiBase).origin ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      return null
    return /^\/api\/conversations\/([^/]+)\/ag-ui\/runs$/.exec(url.pathname)?.[1] ?? null
  } catch {
    return null
  }
}

export function acceptedAgUiStream(
  status: number,
  headers: Headers,
  runId: string | undefined,
): MoldyAgUiAcceptance {
  const mediaType = headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase()
  if (status !== 200 || mediaType !== 'text/event-stream' || !runId)
    throw new Error(`The AG-UI command did not open an accepted event stream (${status}).`)
  if (headers.get('X-Run-Id') !== runId) throw new Error('Missing or mismatched public run ID.')
  const receipt = readAcceptance(headers, runId)
  if (!receipt.physicalRunId && !receipt.queue)
    throw new Error('The AG-UI command did not identify a run or queued input.')
  return receipt
}

/** Read only the command identity and receipt when headers arrive, before requestfailed fires. */
export function hasAcceptedAgUiFetch(
  request: Pick<Request, 'url' | 'method' | 'resourceType' | 'postData' | 'redirectedFrom'>,
  response: Pick<Response, 'status' | 'headers' | 'fromServiceWorker'>,
  apiBase: string,
): boolean {
  if (request.resourceType() !== 'fetch' || response.status() !== 200) return false
  const thread = agUiCommandThread(request, apiBase)
  if (!thread || response.fromServiceWorker() || request.redirectedFrom()) return false
  try {
    const input = commandIdentity.safeParse(JSON.parse(request.postData() ?? ''))
    if (!input.success || input.data.threadId !== thread || !input.data.runId.trim()) return false
    acceptedAgUiStream(response.status(), new Headers(response.headers()), input.data.runId)
    return true
  } catch {
    return false
  }
}
