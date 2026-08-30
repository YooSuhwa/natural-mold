import { HttpAgentServerAdapter, type AgentServerAdapter } from '@langchain/react'
import { API_BASE, fireSessionExpired } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'

const MUTATION_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])

export type RunStartAcceptedListener = (runId?: string) => void

export interface MoldyAgentTransportOptions {
  apiBase?: string
  fetch?: typeof fetch
  onState?: (state: AgentServerState<unknown>) => void
  onRunStartAccepted?: RunStartAcceptedListener
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
  setStateHydrationListener(listener: MoldyAgentTransportOptions['onState']): void
  setRunStartAcceptedListener(listener: RunStartAcceptedListener | undefined): void
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

type ProtocolCommand = Parameters<HttpAgentServerAdapter['send']>[0]
type ProtocolSendResult = ReturnType<HttpAgentServerAdapter['send']>
type EventStreamParams = Parameters<NonNullable<AgentServerAdapter['openEventStream']>>[0]
type EventStreamHandle = ReturnType<NonNullable<AgentServerAdapter['openEventStream']>>

function acceptedRunId(value: unknown): string | undefined {
  if (typeof value !== 'object' || value === null) return undefined
  if (!('type' in value) || value.type !== 'success') return undefined
  const result = 'result' in value ? value.result : undefined
  if (typeof result !== 'object' || result === null || !('run_id' in result)) return undefined
  return typeof result.run_id === 'string' && result.run_id.trim() ? result.run_id : undefined
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
  readonly #delegate: HttpAgentServerAdapter
  #onState: MoldyAgentTransportOptions['onState']
  #onRunStartAccepted: MoldyAgentTransportOptions['onRunStartAccepted']
  readonly #stateHydrationListeners = new Set<StateHydrationListener>()
  #latestState: AgentServerState<unknown> | undefined
  threadId: string

  constructor(
    agentId: string,
    options: ConstructorParameters<typeof HttpAgentServerAdapter>[0],
    onState?: MoldyAgentTransportOptions['onState'],
    onRunStartAccepted?: MoldyAgentTransportOptions['onRunStartAccepted'],
  ) {
    this.#agentId = agentId
    this.#delegate = new HttpAgentServerAdapter(options)
    this.#onState = onState
    this.#onRunStartAccepted = onRunStartAccepted
    this.threadId = this.#delegate.threadId
  }

  setThreadId(threadId: string): void {
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

  events(): ReturnType<AgentServerAdapter['events']> {
    return this.#delegate.events()
  }

  async getState<StateType = unknown>(): Promise<AgentServerState<StateType>> {
    const state = (await this.#delegate.getState?.<StateType>()) ?? null
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
    return this.#delegate.openEventStream(params)
  }

  close(): Promise<void> {
    this.#stateHydrationListeners.clear()
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
      fetch: authedFetch,
      paths: {
        commands: (threadId) => langGraphThreadPath(conversationId, threadId, '/commands'),
        stream: (threadId) => langGraphThreadPath(conversationId, threadId, '/stream/events'),
        state: (threadId) => langGraphThreadPath(conversationId, threadId, '/state'),
      },
    },
    options.onState,
    options.onRunStartAccepted,
  )
}
