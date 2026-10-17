/**
 * Native `auth_required` interrupt (org-authz plan 11.5, F-07).
 *
 * The backend ConnectionGuard pauses a run with ONE bundled payload
 * `{type: 'auth_required', reason, items, allowed_decisions: ['respond']}`. It is
 * normalized into a respond-only `auth_required` action so the standard HITL path
 * (decision coordinator, resume) carries it; the resume answer is the existing
 * `Decision` `{type: 'respond', message: 'retry' | 'skip' | 'cancel'}`.
 */
import type { StandardInterruptPayload } from '@/lib/types'

// Synthetic tool-call name; namespaced so a real tool called `auth_required` never renders it.
export const AUTH_REQUIRED_ACTION = 'moldy_auth_required'
export const AUTH_REQUIRED_RESPONSES = ['retry', 'skip', 'cancel'] as const
export type AuthRequiredResponse = (typeof AUTH_REQUIRED_RESPONSES)[number]

export interface AuthRequiredItem {
  readonly resourceName: string
  readonly toolNames: readonly string[]
  readonly fixableBy: 'me' | 'owner'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseItem(value: unknown): AuthRequiredItem | null {
  if (!isRecord(value)) return null
  const name = typeof value.resource_name === 'string' ? value.resource_name : ''
  if (!name) return null
  const tools = Array.isArray(value.tool_names)
    ? value.tool_names.filter((tool): tool is string => typeof tool === 'string')
    : []
  return {
    resourceName: name,
    toolNames: tools,
    fixableBy: value.fixable_by === 'owner' ? 'owner' : 'me',
  }
}

/** Items of an `auth_required` payload or tool-call args (streaming-safe: never throws). */
export function authRequiredItems(args: unknown): AuthRequiredItem[] {
  if (!isRecord(args) || !Array.isArray(args.items)) return []
  return args.items.map(parseItem).filter((item): item is AuthRequiredItem => item !== null)
}

export function nativeAuthRequiredPayload(
  interruptId: string,
  value: Record<string, unknown>,
): StandardInterruptPayload | null {
  if (value.type !== 'auth_required') return null
  const reason = typeof value.reason === 'string' ? value.reason : 'auth_needed'
  const items = Array.isArray(value.items) ? value.items : []
  // A still-broken `retry` re-asks under the same LangGraph interrupt id; `attempt`
  // tells the re-ask apart (card identity, resolved-interrupt bookkeeping).
  const attempt = typeof value.attempt === 'number' ? value.attempt : 1
  return {
    interrupt_id: interruptId,
    native: 'auth_required',
    action_requests: [{ name: AUTH_REQUIRED_ACTION, args: { reason, items, attempt } }],
    review_configs: [{ action_name: AUTH_REQUIRED_ACTION, allowed_decisions: ['respond'] }],
  }
}

function attemptOf(args: Record<string, unknown> | undefined): number | null {
  return typeof args?.attempt === 'number' ? args.attempt : null
}

/** Interrupt identity for dedup/resolution: the id, plus the attempt of an auth re-ask. */
export function interruptIdentity(payload: StandardInterruptPayload): string {
  const attempt =
    payload.native === 'auth_required' ? attemptOf(payload.action_requests[0]?.args) : null
  return attempt === null ? payload.interrupt_id : `${payload.interrupt_id}#${attempt}`
}

/** Same identity, read back from a resolved synthetic tool call's args. */
export function resolvedIdentity(name: string, args: Record<string, unknown>): string {
  const id = String(args.hitl_interrupt_id ?? '')
  const attempt = name === AUTH_REQUIRED_ACTION ? attemptOf(args) : null
  return attempt === null ? id : `${id}#${attempt}`
}
