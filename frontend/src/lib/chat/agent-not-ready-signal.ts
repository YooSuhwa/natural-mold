/**
 * `409 agent_not_ready` from a chat start/send/resume (plan 12.3, 13.12.1; G-02).
 *
 * The LangGraph protocol transport only surfaces a plain `Error` for non-2xx
 * commands, so the code is read where the response is still at hand (the
 * transport fetch wrapper, the SSE `onopen`) and announced here with where it came
 * from. The chat screen's readiness gate opens the S21 panel only for its own agent.
 */
import { parseApiErrorBody } from '@/lib/api/errors'
import type { AgentReadiness } from '@/lib/types/readiness'

export const AGENT_NOT_READY = 'agent_not_ready'

/** Which run the refusal belongs to: the transport's agent/conversation, or the path's. */
export interface AgentNotReadyOrigin {
  readonly agentId?: string | null
  readonly conversationId?: string | null
}

export interface AgentNotReadySignal extends AgentNotReadyOrigin {
  readonly details: AgentReadiness | null
}

type Listener = (signal: AgentNotReadySignal) => void

const listeners = new Set<Listener>()

export function onAgentNotReady(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function announceAgentNotReady(signal: AgentNotReadySignal): void {
  for (const listener of listeners) listener(signal)
}

/**
 * Whether a refusal is about this screen's agent: the body's `agent_id` (sent to config
 * viewers) or the origin's agent wins; a conversation id alone must match the screen's.
 */
export function signalMatches(
  signal: AgentNotReadySignal,
  agentId: string,
  conversationId?: string | null,
): boolean {
  const agent = signal.details?.agent_id ?? signal.agentId
  if (agent) return agent === agentId
  return !!signal.conversationId && signal.conversationId === conversationId
}

/** The agent or conversation an SSE path names (`/api/agents/{id}/…`, `/api/conversations/{id}/…`). */
export function originOfPath(path: string): AgentNotReadyOrigin {
  const agent = /^\/api\/agents\/([^/?]+)/.exec(path)?.[1]
  const conversation = /^\/api\/conversations\/([^/?]+)/.exec(path)?.[1]
  return { agentId: agent ?? null, conversationId: conversation ?? null }
}

function readinessOf(details: Record<string, unknown> | null): AgentReadiness | null {
  if (!details || !Array.isArray(details.items) || typeof details.overall !== 'string') {
    return null
  }
  return details as unknown as AgentReadiness
}

/** Announces a 409 `agent_not_ready` response; reads a clone, the body stays readable. */
export async function announceIfAgentNotReady(
  response: Response,
  origin: AgentNotReadyOrigin = {},
): Promise<void> {
  if (response.status !== 409) return
  const { code, details } = await parseApiErrorBody(response, { clone: true })
  if (code === AGENT_NOT_READY) announceAgentNotReady({ ...origin, details: readinessOf(details) })
}
