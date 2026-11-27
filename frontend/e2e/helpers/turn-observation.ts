import type { APIRequestContext, Page, Request, Response, TestInfo } from '@playwright/test'
import {
  summarizeTurns,
  turnRecord,
  turnKind,
  type ObservedTurnCommand,
} from './turn-observation-summary'

type Stage =
  | 'starting'
  | 'first-saved'
  | 'second-sent'
  | 'second-visible'
  | 'both-saved'
  | 'both-restored'

async function read(request: APIRequestContext, url: string) {
  try {
    const response = await request.get(url, { timeout: 2500, maxRedirects: 0 })
    return {
      status: response.status(),
      data: response.ok() ? turnRecord(await response.json()) : {},
    }
  } catch {
    return { status: 0, data: {} as Record<string, unknown> }
  }
}

/** Keep command receipts, saved turns, and rendered rows comparable without emitting user data. */
export function observeChatTurns(page: Page, api: string) {
  let stage: Stage = 'starting'
  const requests = new Map<Request, ObservedTurnCommand>()
  function started(req: Request) {
    if (req.method() !== 'POST' || requests.size >= 8) return
    const url = new URL(req.url())
    if (url.origin !== new URL(api).origin && url.origin !== new URL(page.url()).origin) return
    const match =
      /^\/api\/conversations\/([^/]+)\/(ag-ui\/runs|langgraph\/threads\/([^/]+)\/commands)$/.exec(
        url.pathname,
      )
    if (!match || (match[3] && match[3] !== match[1])) return
    let body: Record<string, unknown>
    try {
      body = turnRecord(req.postDataJSON())
    } catch {
      return
    }
    const wire = match[2] === 'ag-ui/runs' ? 'ag_ui' : 'v3'
    if (wire === 'v3' && body.method !== 'run.start') return
    const params = turnRecord(body.params)
    const strategy =
      wire === 'ag_ui'
        ? turnRecord(turnRecord(body.forwardedProps).moldy).strategy
        : params.multitask_strategy
    const id = wire === 'ag_ui' ? body.runId : params.client_request_id
    requests.set(req, {
      thread: match[1],
      wire,
      strategy: strategy == null ? 'normal' : turnKind(strategy, ['enqueue', 'interrupt']),
      id: typeof id === 'string' ? id : undefined,
      status: 0,
    })
  }
  function received(response: Response) {
    const entry = requests.get(response.request())
    if (!entry) return
    const headers = response.headers()
    entry.status = response.status()
    entry.receiptInput = headers['x-moldy-input-id']
    entry.receiptRun = headers['x-moldy-run-id']
  }
  function dispose() {
    page.off('request', started)
    page.off('response', received)
  }
  page.on('request', started)
  page.on('response', received)
  return {
    reached(value: Stage) {
      stage = value
    },
    dispose,
    async attach(info: TestInfo, request: APIRequestContext, conversationId: string) {
      dispose()
      try {
        const base = `${api}/api/conversations/${encodeURIComponent(conversationId)}`
        const [messages, history, inputs, renderedUsers, renderedAssistants] = await Promise.all([
          read(request, base + '/messages'),
          read(request, base + '/history'),
          read(request, base + '/run-inputs'),
          page.locator('[data-moldy-message-role="user"]:visible').count(),
          page.locator('[data-moldy-message-role="assistant"]:visible').count(),
        ])
        await info.attach('chat-turn-observations', {
          contentType: 'application/json',
          body: Buffer.from(
            JSON.stringify({
              stage,
              ...summarizeTurns(
                conversationId,
                [...requests.values()],
                messages.data,
                history.data,
                inputs.data,
              ),
              messagesStatus: messages.status,
              historyStatus: history.status,
              inputsStatus: inputs.status,
              renderedUsers: Math.min(64, renderedUsers),
              renderedAssistants: Math.min(64, renderedAssistants),
            }),
          ),
        })
      } catch {
        // The diagnostic must preserve the result of the original two-turn assertions.
      }
    },
  }
}
