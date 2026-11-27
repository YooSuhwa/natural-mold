import type { RunAgentInput } from '@ag-ui/core'
import { RunAgentInputSchema } from '@ag-ui/core/schemas'
import type { APIRequestContext, Request, Response } from '@playwright/test'

import { readAcceptance, type MoldyAgUiAcceptance } from '../../src/lib/ag-ui/moldy-agent-receipt'
import { API_BASE, apiGetJson, expect, isRecord } from '../fixtures'

type RunRequest = Pick<Request, 'url' | 'method' | 'postData'>
type RunResponse = Pick<Response, 'status' | 'allHeaders'> & { request(): RunRequest }

/** Match the authenticated run endpoint and the standard input for that same conversation. */
export function readAgUiRunInput(req: RunRequest, conversationId?: string): RunAgentInput | null {
  if (req.method() !== 'POST') return null
  const url = new URL(req.url())
  if (
    url.origin !== new URL(API_BASE).origin ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    return null
  }
  const thread = /^\/api\/conversations\/([^/]+)\/ag-ui\/runs$/.exec(url.pathname)?.[1]
  if (!thread || (conversationId !== undefined && thread !== conversationId)) return null
  const body = req.postData()
  if (!body) return null
  let value: unknown
  try {
    value = JSON.parse(body)
  } catch {
    return null
  }
  if (!isRecord(value) || value.protocolVersion !== '1.0') return null
  const parsed = RunAgentInputSchema.safeParse(value)
  if (!parsed.success) return null
  const input = parsed.data
  return input.threadId === thread && input.runId.trim() ? input : null
}

/** Read the receipt at stream open, including pending inputs with no worker run yet. */
export async function readAgUiAcceptance(response: RunResponse): Promise<MoldyAgUiAcceptance> {
  const input = readAgUiRunInput(response.request())
  const headers = new Headers(await response.allHeaders())
  const mediaType = headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase()
  if (response.status() !== 200 || mediaType !== 'text/event-stream' || !input) {
    throw new Error(
      `The AG-UI command did not open an accepted event stream (${response.status()}).`,
    )
  }
  if (headers.get('X-Run-Id') !== input.runId)
    throw new Error('Missing or mismatched public run ID.')
  const acceptance = readAcceptance(headers, input.runId)
  if (!acceptance.physicalRunId && !acceptance.queue) {
    throw new Error('The AG-UI command did not identify a run or queued input.')
  }
  return acceptance
}

export async function acceptedPhysicalRun(
  request: APIRequestContext,
  response: Response,
): Promise<string> {
  const acceptance = await readAgUiAcceptance(response)
  if (acceptance.physicalRunId) return acceptance.physicalRunId
  const input = readAgUiRunInput(response.request())
  if (!input || !acceptance.queue) throw new Error('Missing AG-UI queued input.')
  const inputId = acceptance.queue.inputId
  let runId = ''
  await expect
    .poll(
      async () => {
        const listing = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${input.threadId}/run-inputs`,
        )
        if (!isRecord(listing) || !Array.isArray(listing.items)) return null
        const row = listing.items.find((item: unknown) => isRecord(item) && item.id === inputId)
        if (
          !isRecord(row) ||
          row.conversation_id !== input.threadId ||
          typeof row.run_id !== 'string'
        )
          return null
        runId = row.run_id
        return runId
      },
      { timeout: 45_000 },
    )
    .toBeTruthy()
  return runId
}
