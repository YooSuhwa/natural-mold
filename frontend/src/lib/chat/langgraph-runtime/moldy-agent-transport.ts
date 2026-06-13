import { HttpAgentServerAdapter } from '@langchain/react'
import { API_BASE, fireSessionExpired } from '@/lib/api/client'
import { csrfStore } from '@/lib/auth/csrf'

const MUTATION_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])

export interface MoldyAgentTransportOptions {
  apiBase?: string
  fetch?: typeof fetch
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

class MoldyHttpAgentServerAdapter extends HttpAgentServerAdapter {
  readonly #agentId: string

  constructor(agentId: string, options: ConstructorParameters<typeof HttpAgentServerAdapter>[0]) {
    super(options)
    this.#agentId = agentId
  }

  override send(command: ProtocolCommand): ProtocolSendResult {
    return super.send(commandWithAgentId(command, this.#agentId))
  }
}

export function createMoldyAgentTransport(
  conversationId: string,
  agentId: string,
  options: MoldyAgentTransportOptions = {},
): HttpAgentServerAdapter {
  const authedFetch = withMoldyAuth(options.fetch ?? fetch)
  return new MoldyHttpAgentServerAdapter(agentId, {
    apiUrl: options.apiBase ?? API_BASE,
    threadId: conversationId,
    fetch: authedFetch,
    paths: {
      commands: (threadId) => langGraphThreadPath(conversationId, threadId, '/commands'),
      stream: (threadId) => langGraphThreadPath(conversationId, threadId, '/stream/events'),
      state: (threadId) => langGraphThreadPath(conversationId, threadId, '/state'),
    },
  })
}
