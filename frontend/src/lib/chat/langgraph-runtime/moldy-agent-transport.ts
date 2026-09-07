import {
  ProtocolSseTransportAdapter,
  type ProtocolSseTransportOptions,
} from '@langchain/langgraph-sdk'
import type { AgentServerAdapter } from '@langchain/react'
import type { AppendMessage } from '@assistant-ui/react'
import { API_BASE, fireSessionExpired } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'
import { queuedInputFromMessage } from '@/lib/chat/message-queue/queue-message-projection'
import type { QueueRunStartAcceptance } from '@/lib/chat/message-queue/server-message-queue-contract'
import { withResourceContextRunInput } from '@/lib/chat/context/resource-context-payload'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'

const MUTATION_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])
const MAX_RECONNECT_ATTEMPTS = 5
let queueCommandId = 0

function nextQueueCommandId(): number {
  queueCommandId += 1
  return queueCommandId
}

export type RunStartAcceptedListener = (runId?: string) => void

export interface MoldyAgentTransportOptions {
  apiBase?: string
  fetch?: typeof fetch
  onState?: (state: AgentServerState<unknown>) => void
  onRunStartAccepted?: RunStartAcceptedListener
  onReconnectStateChange?: (state: 'idle' | 'reconnecting') => void
  reconnectDelayMs?: ProtocolSseTransportOptions['reconnectDelayMs']
}

type AgentServerState<StateType = unknown> = {
  values: StateType
  next?: unknown
  tasks?: unknown
  metadata?: unknown
  checkpoint?: { checkpoint_id?: string } | null
  parent_checkpoint?: { checkpoint_id?: string } | null
} | null

type StateHydrationListener = (state: AgentServerState<unknown>) => void

export interface MoldyAgentServerAdapter extends AgentServerAdapter {
  activateStateHydration(): () => void
  readState<StateType = unknown>(): Promise<AgentServerState<StateType>>
  setStateHydrationListener(listener: MoldyAgentTransportOptions['onState']): void
  setRunStartAcceptedListener(listener: RunStartAcceptedListener | undefined): void
  submitQueuedInput(
    message: AppendMessage,
    strategy: 'enqueue' | 'interrupt',
    requestId: string,
  ): Promise<QueueRunStartAcceptance>
  retryFailedInput(input: ConversationRunInput, requestId: string): Promise<QueueRunStartAcceptance>
}

function encodePathSegment(value: string): string {
  return encodeURIComponent(value)
}

function langGraphThreadPath(conversationId: string, threadId: string, suffix: string): string {
  const conversation = encodePathSegment(conversationId)
  const thread = encodePathSegment(threadId)
  return `/api/conversations/${conversation}/langgraph/threads/${thread}${suffix}`
}

function withMoldyAuth(baseFetch: typeof fetch): typeof fetch {
  return async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = new Headers(init?.headers)
    if (MUTATION_METHODS.has(method) && !headers.has('X-CSRF-Token')) {
      const csrf = csrfStore.get()
      if (csrf) headers.set('X-CSRF-Token', csrf)
    }

    const response = await baseFetch(input, {
      ...init,
      method,
      credentials: 'include',
      headers,
    })
    if (response.status === 401) fireSessionExpired()
    return response
  }
}

type ProtocolCommand = Parameters<ProtocolSseTransportAdapter['send']>[0]
type ProtocolSendResult = ReturnType<ProtocolSseTransportAdapter['send']>
type EventStreamParams = Parameters<NonNullable<AgentServerAdapter['openEventStream']>>[0]
type EventStreamHandle = ReturnType<NonNullable<AgentServerAdapter['openEventStream']>>

function queuedRunStartCommand(
  agentId: string,
  message: AppendMessage,
  strategy: 'enqueue' | 'interrupt',
  requestId: string,
): ProtocolCommand {
  const contextualInput = withResourceContextRunInput(
    queuedInputFromMessage(message),
    message.runConfig?.custom,
  )
  if (contextualInput.kind === 'invalid') {
    throw new Error(`Resource context cannot be submitted: ${contextualInput.reason}`)
  }
  // Moldy's server extends the installed protocol's run.start params with
  // durable queue fields that @langchain/protocol 0.0.19 does not type yet.
  return {
    id: nextQueueCommandId(),
    method: 'run.start',
    params: {
      assistant_id: agentId,
      input: contextualInput.input,
      multitask_strategy: strategy,
      client_request_id: requestId,
    },
  } as ProtocolCommand
}

function failedInputRunStartCommand(
  agentId: string,
  input: ConversationRunInput,
  requestId: string,
): ProtocolCommand {
  if (!input.run_id || (input.status !== 'claimed' && input.status !== 'failed')) {
    throw new Error('Only an accepted failed run input can be retried')
  }
  const contextualInput = withResourceContextRunInput(
    input.input_payload,
    input.resource_context.length > 0 ? { resource_context: input.resource_context } : undefined,
  )
  if (contextualInput.kind === 'invalid') {
    throw new Error(`Resource context cannot be retried: ${contextualInput.reason}`)
  }
  return {
    id: nextQueueCommandId(),
    method: 'run.start',
    params: {
      assistant_id: agentId,
      input: contextualInput.input,
      multitask_strategy: 'enqueue',
      client_request_id: requestId,
    },
  } as ProtocolCommand
}

function acceptedRunId(value: unknown): string | undefined {
  if (typeof value !== 'object' || value === null) return undefined
  if (!('type' in value) || value.type !== 'success') return undefined
  const result = 'result' in value ? value.result : undefined
  if (typeof result !== 'object' || result === null || !('run_id' in result)) return undefined
  return typeof result.run_id === 'string' && result.run_id.trim() ? result.run_id : undefined
}

function queuedRunStartAcceptance(value: unknown): QueueRunStartAcceptance {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('type' in value) ||
    value.type !== 'success'
  ) {
    throw new Error('Queue submission was not accepted')
  }
  const result = 'result' in value ? value.result : undefined
  if (typeof result !== 'object' || result === null) {
    throw new Error('Queue submission response is missing its result')
  }
  const inputId = 'input_id' in result ? result.input_id : undefined
  const inputStatus = 'input_status' in result ? result.input_status : undefined
  const revision = 'revision' in result ? result.revision : undefined
  const position = 'position' in result ? result.position : undefined
  const runId = 'run_id' in result ? result.run_id : undefined
  if (
    typeof inputId !== 'string' ||
    !inputId.trim() ||
    (inputStatus !== 'pending' && inputStatus !== 'claimed') ||
    typeof revision !== 'number' ||
    !Number.isInteger(revision) ||
    typeof position !== 'number' ||
    !Number.isInteger(position) ||
    revision < 1 ||
    position < 1 ||
    (runId != null && (typeof runId !== 'string' || !runId.trim()))
  ) {
    throw new Error('Queue submission response is malformed')
  }
  return {
    inputId,
    inputStatus,
    revision,
    position,
    ...(typeof runId === 'string' && runId.trim() ? { runId } : {}),
  }
}

function commandWithAgentId(command: ProtocolCommand, agentId: string): ProtocolCommand {
  if (command.method !== 'run.start') return command
  return {
    ...command,
    params: {
      ...command.params,
      assistant_id: agentId,
    },
  }
}

function registerLangGraphClientDefaults(apiUrl: string, fetchImpl: typeof fetch): void {
  Object.defineProperty(globalThis, Symbol.for('langgraph_api:url'), {
    configurable: true,
    value: apiUrl,
    writable: true,
  })
  Object.defineProperty(globalThis, Symbol.for('langgraph_api:fetch'), {
    configurable: true,
    value: fetchImpl,
    writable: true,
  })
}

class MoldyHttpAgentServerAdapter implements MoldyAgentServerAdapter {
  readonly #agentId: string
  readonly #delegate: ProtocolSseTransportAdapter
  #onState: MoldyAgentTransportOptions['onState']
  #onRunStartAccepted: MoldyAgentTransportOptions['onRunStartAccepted']
  readonly #onReconnectStateChange: MoldyAgentTransportOptions['onReconnectStateChange']
  readonly #stateHydrationListeners = new Set<StateHydrationListener>()
  #latestState: AgentServerState<unknown> | undefined
  #reconnecting = false
  threadId: string

  constructor(
    agentId: string,
    options: ProtocolSseTransportOptions,
    onState?: MoldyAgentTransportOptions['onState'],
    onRunStartAccepted?: MoldyAgentTransportOptions['onRunStartAccepted'],
    onReconnectStateChange?: MoldyAgentTransportOptions['onReconnectStateChange'],
  ) {
    this.#agentId = agentId
    this.#delegate = new ProtocolSseTransportAdapter({
      ...options,
      maxReconnectAttempts: MAX_RECONNECT_ATTEMPTS,
      onReconnect: () => this.#setReconnectState('reconnecting'),
    })
    this.#onState = onState
    this.#onRunStartAccepted = onRunStartAccepted
    this.#onReconnectStateChange = onReconnectStateChange
    this.threadId = this.#delegate.threadId
  }

  #setReconnectState(state: 'idle' | 'reconnecting'): void {
    const reconnecting = state === 'reconnecting'
    if (this.#reconnecting === reconnecting) return
    this.#reconnecting = reconnecting
    this.#onReconnectStateChange?.(state)
  }

  setThreadId(threadId: string): void {
    this.#setReconnectState('idle')
    this.#delegate.setThreadId(threadId)
    this.threadId = this.#delegate.threadId
  }

  setRunStartAcceptedListener(listener: RunStartAcceptedListener | undefined): void {
    this.#onRunStartAccepted = listener
  }

  setStateHydrationListener(listener: MoldyAgentTransportOptions['onState']): void {
    this.#onState = listener
  }

  open(): Promise<void> {
    return this.#delegate.open()
  }

  async send(command: ProtocolCommand): Promise<Awaited<ProtocolSendResult>> {
    const value = await this.#delegate.send(commandWithAgentId(command, this.#agentId))
    const runId = command.method === 'run.start' ? acceptedRunId(value) : undefined
    if (runId) {
      this.#onRunStartAccepted?.(runId)
    }
    return value
  }

  async submitQueuedInput(
    message: AppendMessage,
    strategy: 'enqueue' | 'interrupt',
    requestId: string,
  ): Promise<QueueRunStartAcceptance> {
    const value = await this.send(
      queuedRunStartCommand(this.#agentId, message, strategy, requestId),
    )
    return queuedRunStartAcceptance(value)
  }

  async retryFailedInput(
    input: ConversationRunInput,
    requestId: string,
  ): Promise<QueueRunStartAcceptance> {
    const value = await this.send(failedInputRunStartCommand(this.#agentId, input, requestId))
    return queuedRunStartAcceptance(value)
  }

  events(): ReturnType<AgentServerAdapter['events']> {
    return this.#delegate.events()
  }

  async readState<StateType = unknown>(): Promise<AgentServerState<StateType>> {
    return (await this.#delegate.getState?.<StateType>()) ?? null
  }

  async getState<StateType = unknown>(): Promise<AgentServerState<StateType>> {
    const state = await this.readState<StateType>()
    this.#latestState = state as AgentServerState<unknown>
    for (const listener of this.#stateHydrationListeners) {
      listener(this.#latestState)
    }
    return state ?? null
  }

  activateStateHydration(): () => void {
    // Per-activation wrapper so each activate/deactivate is tracked independently.
    // Under StrictMode double-activate, sharing a single stored listener reference
    // means the first deactivate() removes the only Set entry and the second
    // activation silently loses its listener.
    const listener: StateHydrationListener = (state) => this.#onState?.(state)
    this.#stateHydrationListeners.add(listener)
    if (this.#latestState !== undefined) {
      listener(this.#latestState)
    }
    return () => {
      this.#stateHydrationListeners.delete(listener)
    }
  }

  openEventStream(params: EventStreamParams): EventStreamHandle {
    this.#setReconnectState('idle')
    const handle = this.#delegate.openEventStream(params)
    const settleReconnect = (): void => this.#setReconnectState('idle')
    return {
      ready: handle.ready,
      events: {
        async *[Symbol.asyncIterator]() {
          try {
            for await (const event of handle.events) {
              settleReconnect()
              yield event
            }
          } finally {
            settleReconnect()
          }
        },
      },
      close: () => {
        handle.close()
        settleReconnect()
      },
    }
  }

  close(): Promise<void> {
    this.#stateHydrationListeners.clear()
    this.#setReconnectState('idle')
    return this.#delegate.close()
  }
}

export function createMoldyAgentTransport(
  conversationId: string,
  agentId: string,
  options: MoldyAgentTransportOptions = {},
): MoldyAgentServerAdapter {
  const authedFetch = withMoldyAuth(options.fetch ?? fetch)
  registerLangGraphClientDefaults(options.apiBase ?? API_BASE, authedFetch)
  return new MoldyHttpAgentServerAdapter(
    agentId,
    {
      apiUrl: options.apiBase ?? API_BASE,
      threadId: conversationId,
      fetchFactory: () => authedFetch,
      reconnectDelayMs: options.reconnectDelayMs,
      paths: {
        commands: (threadId) => langGraphThreadPath(conversationId, threadId, '/commands'),
        stream: (threadId) => langGraphThreadPath(conversationId, threadId, '/stream/events'),
        state: (threadId) => langGraphThreadPath(conversationId, threadId, '/state'),
      },
    },
    options.onState,
    options.onRunStartAccepted,
    options.onReconnectStateChange,
  )
}
