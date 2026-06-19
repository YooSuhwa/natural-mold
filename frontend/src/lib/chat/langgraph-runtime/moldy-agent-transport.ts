import { HttpAgentServerAdapter, type AgentServerAdapter } from '@langchain/react'
import { API_BASE, fireSessionExpired } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'

const MUTATION_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])

export interface MoldyAgentTransportOptions {
  apiBase?: string
  fetch?: typeof fetch
  onState?: (state: AgentServerState<unknown>) => void
  onRunStartAccepted?: () => void
}

type AgentServerState<StateType = unknown> = {
  values: StateType
  next?: unknown
  tasks?: unknown
  metadata?: unknown
  checkpoint?: { checkpoint_id?: string } | null
  parent_checkpoint?: { checkpoint_id?: string } | null
} | null

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

class MoldyHttpAgentServerAdapter implements AgentServerAdapter {
  readonly #agentId: string
  readonly #delegate: HttpAgentServerAdapter
  readonly #onState: MoldyAgentTransportOptions['onState']
  readonly #onRunStartAccepted: MoldyAgentTransportOptions['onRunStartAccepted']
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

  open(): Promise<void> {
    return this.#delegate.open()
  }

  async send(command: ProtocolCommand): Promise<Awaited<ProtocolSendResult>> {
    const value = await this.#delegate.send(commandWithAgentId(command, this.#agentId))
    if (command.method === 'run.start') {
      this.#onRunStartAccepted?.()
    }
    return value
  }

  events(): ReturnType<AgentServerAdapter['events']> {
    return this.#delegate.events()
  }

  async getState<StateType = unknown>(): Promise<AgentServerState<StateType>> {
    const state = (await this.#delegate.getState?.<StateType>()) ?? null
    this.#onState?.(state as AgentServerState<unknown>)
    return state ?? null
  }

  openEventStream(params: EventStreamParams): EventStreamHandle {
    return this.#delegate.openEventStream(params)
  }

  close(): Promise<void> {
    return this.#delegate.close()
  }
}

export function createMoldyAgentTransport(
  conversationId: string,
  agentId: string,
  options: MoldyAgentTransportOptions = {},
): AgentServerAdapter {
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
