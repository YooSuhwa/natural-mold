import type { RunAgentInput } from '@ag-ui/core'
import { RunAgentInputSchema } from '@ag-ui/core/schemas'
import type { APIRequestContext, Request, Response } from '@playwright/test'

import type { MoldyAgUiAcceptance } from '../../src/lib/ag-ui/moldy-agent-receipt'
import { API_BASE, apiGetJson, expect, isRecord } from '../fixtures'
import { acceptedAgUiStream, agUiCommandThread } from './ag-ui-stream-acceptance'

type RunRequest = Pick<Request, 'url' | 'method' | 'postData'>
type RunResponse = Pick<Response, 'status' | 'allHeaders'> & { request(): RunRequest }

/** Match the authenticated run endpoint and the standard input for that same conversation. */
export function readAgUiRunInput(req: RunRequest, conversationId?: string): RunAgentInput | null {
  const thread = agUiCommandThread(req, API_BASE)
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
  return acceptedAgUiStream(response.status(), headers, input?.runId)
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
