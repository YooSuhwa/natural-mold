import type { Page, Request, Response } from '@playwright/test'

import {
  acceptedRunId,
  commandMethodFromRequest,
  isConversationRunStartUrl,
  parseRunStartResponseBody,
} from '../../scripts/e2e-run-start-response'
import { API_BASE } from '../fixtures'

const RUN_START_RESPONSE_TIMEOUT_MS = 45_000

export function commandMethod(request: Request): string | null {
  return commandMethodFromRequest(request.method(), request.url(), request.postData())
}

function isRunStartForConversation(response: Response, conversationId: string): boolean {
  const request = response.request()
  return (
    request.method() === 'POST' &&
    isConversationRunStartUrl(response.url(), API_BASE, conversationId) &&
    commandMethod(request) === 'run.start'
  )
}

async function responseRunId(response: Response): Promise<string> {
  if (!response.ok()) throw new Error('run.start command did not succeed')
  return acceptedRunId({
    ok: response.ok(),
    runIdHeader: await response.headerValue('X-Run-Id'),
    body: parseRunStartResponseBody(await response.body()),
  })
}

/** Arms a conversation-scoped run.start response waiter before invoking the user action. */
export async function waitForAcceptedRunStart(
  page: Page,
  conversationId: string,
  action: () => Promise<void>,
): Promise<string> {
  const responsePromise = page.waitForResponse(
    (response) => isRunStartForConversation(response, conversationId),
    { timeout: RUN_START_RESPONSE_TIMEOUT_MS },
  )
  const [response] = await Promise.all([responsePromise, action()])
  return responseRunId(response)
}
