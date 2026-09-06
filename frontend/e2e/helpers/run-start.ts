import type { Page, Request, Response } from '@playwright/test'

import {
  acceptedRunStart,
  commandMethodFromRequest,
  parseConversationRunStartUrl,
  parseRunStartResponseBody,
  type AcceptedRunStart,
} from '../../scripts/e2e-run-start-response'
import { API_BASE } from '../fixtures'

const RUN_START_RESPONSE_TIMEOUT_MS = 45_000

export function commandMethod(request: Request): string | null {
  return commandMethodFromRequest(request.method(), request.url(), request.postData())
}

function isGenericRunStart(response: Response): boolean {
  const request = response.request()
  return (
    request.method() === 'POST' &&
    parseConversationRunStartUrl(response.url(), API_BASE) !== null &&
    commandMethod(request) === 'run.start'
  )
}

function isRunStartForConversation(response: Response, conversationId: string): boolean {
  return (
    isGenericRunStart(response) &&
    parseConversationRunStartUrl(response.url(), API_BASE) === conversationId
  )
}

async function acceptedResponse(response: Response): Promise<AcceptedRunStart> {
  const requestConversationId = parseConversationRunStartUrl(response.url(), API_BASE)
  if (!requestConversationId) {
    throw new Error('run.start command did not use the expected conversation endpoint')
  }
  return acceptedRunStart({
    ok: response.ok(),
    requestConversationId,
    runIdHeader: await response.headerValue('X-Run-Id'),
    body: parseRunStartResponseBody(await response.body()),
  })
}

async function waitForRunStartResponse(
  page: Page,
  action: () => Promise<void>,
  matches: (response: Response) => boolean,
): Promise<AcceptedRunStart> {
  const responsePromise = page.waitForResponse(matches, { timeout: RUN_START_RESPONSE_TIMEOUT_MS })
  const [response] = await Promise.all([responsePromise, action()])
  return acceptedResponse(response)
}

/** Arms a generic run.start response waiter before invoking the user action. */
export async function waitForAcceptedRunStartResponse(
  page: Page,
  action: () => Promise<void>,
): Promise<AcceptedRunStart> {
  return waitForRunStartResponse(page, action, isGenericRunStart)
}

/** Arms a conversation-scoped run.start response waiter before invoking the user action. */
export async function waitForAcceptedRunStart(
  page: Page,
  conversationId: string,
  action: () => Promise<void>,
): Promise<string> {
  const accepted = await waitForRunStartResponse(page, action, (response) =>
    isRunStartForConversation(response, conversationId),
  )
  return accepted.runId
}
