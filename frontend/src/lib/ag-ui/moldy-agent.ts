import {
  enforceOutgoingInput,
  HttpAgent,
  runHttpRequest,
  transformHttpEventStream,
  type AgentSubscriber,
} from '@ag-ui/client'
import type { BaseEvent, RunAgentInput } from '@ag-ui/core'
import { defer, EMPTY, switchMap, tap } from 'rxjs'
import { apiFetchResponse } from '@/lib/api/client'
import { throwApiError } from '@/lib/api/errors'
import { announceIfAgentNotReady } from '@/lib/chat/agent-not-ready-signal'
import type { ResourceContextReference } from '@/lib/chat/context/resource-context'
import type { QueueRunStartAcceptance } from '@/lib/chat/message-queue/server-message-queue-contract'
import {
  anySignal,
  currentOrgScope,
  isOrgScopeCurrent,
  signalForOrgScope,
  type OrgScopeToken,
} from '@/lib/org-scope/org-scope-signal'
import { parseWireChatSnapshot, type WireChatSnapshot } from './chat-history-types'
import { moldyCommandInput, type MoldyRunOptions } from './moldy-agent-input'
import { readAcceptance, type MoldyAgUiAcceptance } from './moldy-agent-receipt'

export type { MoldyAgUiAcceptance }

export interface MoldyAgUiAgentOptions {
  readonly conversationId: string
  readonly onBeforeSubmit?: () => void
  readonly onAccepted?: (receipt: MoldyAgUiAcceptance) => void
  readonly onPublicEvent?: (event: BaseEvent, context: { source: 'run' | 'attach' }) => void
}

interface ConversationSession {
  options: MoldyAgUiAgentOptions
  path: string
  scope: OrgScopeToken
  lifetime: AbortController
  selection: string | null | undefined
  generation: number
}

interface QueuedMessage {
  readonly id: string
  readonly text: string
  readonly attachmentIds: readonly string[]
  readonly resourceContext?: { readonly resources: readonly ResourceContextReference[] }
}

type RuntimeParameters = Parameters<HttpAgent['runAgent']>[0]

/** Moldy owns execution. This agent attaches the official SDK to a single authorized conversation. */
export class MoldyAgUiAgent extends HttpAgent {
  private session: ConversationSession

  constructor(options: MoldyAgUiAgentOptions) {
    const path = '/api/conversations/' + encodeURIComponent(options.conversationId) + '/ag-ui'
    super({ threadId: options.conversationId, url: path + '/runs' })
    this.session = {
      options,
      path,
      scope: currentOrgScope(),
      lifetime: new AbortController(),
      selection: undefined,
      generation: 0,
    }
  }

  override clone(): MoldyAgUiAgent {
    const copy = super.clone() as MoldyAgUiAgent
    copy.session = this.session
    return copy
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    const session = this.session
    const local = init.signal
      ? anySignal([session.lifetime.signal, init.signal])
      : session.lifetime.signal
    const signal = signalForOrgScope(session.scope, path, local) ?? local
    signal.throwIfAborted()
    const send = () => apiFetchResponse(path, { ...init, signal, cache: 'no-store' })
    let response: Response
    try {
      response = await send()
    } catch (error) {
      // The acceptance may be durable despite a lost response. Replay the exact
      // command only before headers; once SSE starts, recovery is read-only.
      signal.throwIfAborted()
      if (init.method !== 'POST' || !(error instanceof TypeError)) throw error
      response = await send()
    }
    if (signal.aborted) {
      void response.body?.cancel().catch(() => {})
      signal.throwIfAborted()
    }
    if (!response.ok) {
      await announceIfAgentNotReady(response, { conversationId: session.options.conversationId })
      await throwApiError(response, 'UNKNOWN_ERROR')
    }
    return response
  }

  async readSnapshot({
    runId,
    signal,
  }: { runId?: string; signal?: AbortSignal } = {}): Promise<WireChatSnapshot> {
    const session = this.session
    const version = runId === undefined ? ++session.generation : session.generation
    const query = runId === undefined ? '' : '?' + new URLSearchParams({ runId })
    const response = await this.request(session.path + '/snapshot' + query, {
      method: 'GET',
      signal,
    })
    const snapshot = parseWireChatSnapshot(await response.json(), session.options.conversationId)
    session.lifetime.signal.throwIfAborted()
    if (!isOrgScopeCurrent(session.scope))
      throw new DOMException('Organization changed', 'AbortError')
    signal?.throwIfAborted()
    if (runId === undefined && version === session.generation) {
      session.selection = snapshot.activeRunId
      this.pendingInterrupts = [...snapshot.interrupts]
    }
    return snapshot
  }

  private async submit(input: RunAgentInput, signal: AbortSignal, activate: boolean) {
    const session = this.session
    const command = enforceOutgoingInput(moldyCommandInput(input, session.options.conversationId), {
      maxEntries: this.maxEnforcedEntries,
    })
    const submissionScope = signalForOrgScope(session.scope, session.path, session.lifetime.signal)
    submissionScope?.throwIfAborted()
    signal.throwIfAborted()
    if (activate) session.options.onBeforeSubmit?.()
    const response = await this.request(session.path + '/runs', {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      body: JSON.stringify(command),
      signal,
    })
    try {
      if (
        response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() !==
        'text/event-stream'
      ) {
        throw new Error('Chat command did not return an event stream.')
      }
      const receipt = readAcceptance(response.headers, command.runId)
      if (activate || receipt.physicalRunId !== null) {
        session.selection = receipt.publicRunId
        session.generation++
      }
      if (activate) session.options.onAccepted?.(receipt)
      return { response, receipt }
    } catch (error) {
      void response.body?.cancel().catch(() => {})
      throw error
    }
  }

  private decode(publicRunId: string, response: () => Promise<Response>) {
    const conversationId = this.session.options.conversationId
    return transformHttpEventStream(runHttpRequest(response), this.debugLogger).pipe(
      tap((event) => {
        if (event.type === 'RUN_STARTED' || event.type === 'RUN_FINISHED') {
          if (
            !('threadId' in event) ||
            event.threadId !== conversationId ||
            !('runId' in event) ||
            event.runId !== publicRunId
          ) {
            throw new Error('Chat stream belongs to another run.')
          }
        }
        if (
          (event.type === 'RUN_FINISHED' || event.type === 'RUN_ERROR') &&
          this.session.selection === publicRunId
        ) {
          // A completed or invalidated reader is no longer authoritative for a
          // later attach. Resolve that selection from the next owned snapshot.
          this.session.selection = undefined
          this.session.generation++
        }
      }),
    )
  }

  override run(input: RunAgentInput) {
    return this.decode(
      input.runId,
      async () => (await this.submit(input, this.abortController.signal, true)).response,
    )
  }

  protected override connect(input: RunAgentInput) {
    const session = this.session
    const signal = this.abortController.signal
    return defer(async () =>
      session.selection === undefined
        ? (await this.readSnapshot({ signal })).activeRunId
        : session.selection,
    ).pipe(
      switchMap((runId) => {
        const requested: unknown = input.forwardedProps?.runConfig?.moldy?.attachRunId
        if (!runId || (requested !== undefined && requested !== runId)) return EMPTY
        const path = session.path + '/stream?' + new URLSearchParams({ runId })
        return this.decode(runId, () =>
          this.request(path, {
            method: 'GET',
            headers: { Accept: 'text/event-stream' },
            signal,
          }),
        )
      }),
    )
  }

  private observe(source: 'run' | 'attach') {
    const session = this.session
    return this.subscribe({
      onEvent: ({ event }) => {
        if (!session.lifetime.signal.aborted && isOrgScopeCurrent(session.scope)) {
          session.options.onPublicEvent?.(event, { source })
        }
      },
    })
  }

  override async runAgent(parameters?: RuntimeParameters, subscriber?: AgentSubscriber) {
    if (this.isRunning) throw new Error('This chat agent is already consuming a run.')
    const observer = this.observe('run')
    try {
      return await super.runAgent(parameters, subscriber)
    } finally {
      observer.unsubscribe()
    }
  }

  override async connectAgent(parameters?: RuntimeParameters, subscriber?: AgentSubscriber) {
    if (this.isRunning) throw new Error('This chat agent is already consuming a run.')
    this.abortController = parameters?.abortController ?? new AbortController()
    const observer = this.observe('attach')
    try {
      return await super.connectAgent(parameters, subscriber)
    } finally {
      observer.unsubscribe()
    }
  }

  private async submitQueueCommand(
    runId: string,
    messages: RunAgentInput['messages'],
    options: MoldyRunOptions,
  ): Promise<QueueRunStartAcceptance> {
    const session = this.session
    const { response, receipt } = await this.submit(
      {
        threadId: session.options.conversationId,
        runId,
        protocolVersion: '1.0',
        messages,
        state: {},
        tools: [],
        context: [],
        forwardedProps: { moldy: options },
      },
      session.lifetime.signal,
      false,
    )
    // A server queue owns this input after acceptance. Closing the body only
    // detaches this subscriber and must not invoke the explicit cancel command.
    void response.body?.cancel().catch(() => {})
    if (!receipt.queue) throw new Error('Missing chat queue acceptance.')
    return receipt.queue
  }

  submitQueuedInput(message: QueuedMessage, strategy: 'enqueue' | 'interrupt', requestId: string) {
    return this.submitQueueCommand(
      requestId,
      [{ role: 'user', id: message.id, content: message.text }],
      {
        strategy,
        ...(message.attachmentIds.length ? { attachmentIds: [...message.attachmentIds] } : {}),
        ...(message.resourceContext
          ? { resourceContext: { resources: [...message.resourceContext.resources] } }
          : {}),
      },
    )
  }

  retryFailedInput(inputId: string, requestId: string) {
    return this.submitQueueCommand(requestId, [], { operation: 'retry', retryInputId: inputId })
  }

  dispose() {
    this.session.lifetime.abort()
    this.abortRun()
  }
}
