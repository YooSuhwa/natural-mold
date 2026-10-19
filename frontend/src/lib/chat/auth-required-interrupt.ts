/**
 * Native `auth_required` interrupt (org-authz plan 11.5, F-07).
 *
 * The backend ConnectionGuard pauses a run with ONE bundled payload
 * `{type: 'auth_required', reason, items, allowed_decisions: ['respond']}`. It is
 * normalized into a respond-only `auth_required` action so the standard HITL path
 * (decision coordinator, resume) carries it; the resume answer is the existing
 * `Decision` `{type: 'respond', message: 'retry' | 'skip' | 'cancel'}`.
 */
import type { Decision, InterruptPayload, StandardInterruptPayload } from '@/lib/types'

// Synthetic tool-call name; namespaced so a real tool called `auth_required` never renders it.
export const AUTH_REQUIRED_ACTION = 'moldy_auth_required'
export const AUTH_REQUIRED_RESPONSES = ['retry', 'skip', 'cancel'] as const
export type AuthRequiredResponse = (typeof AUTH_REQUIRED_RESPONSES)[number]

export type AuthConnectKind = 'oauth' | 'api_key' | 'binding'

export interface AuthRequiredItem {
  readonly resourceName: string
  readonly toolNames: readonly string[]
  readonly fixableBy: 'me' | 'owner'
  /** The credential to reconnect (`POST /api/oauth2-credential/auth/{id}` for `oauth`). */
  readonly credentialId: string | null
  readonly connectKind: AuthConnectKind | null
}

function connectKindOf(value: unknown): AuthConnectKind | null {
  const kind = isRecord(value) ? value.kind : null
  return kind === 'oauth' || kind === 'api_key' || kind === 'binding' ? kind : null
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
    credentialId:
      typeof value.credential_id === 'string' && value.credential_id ? value.credential_id : null,
    connectKind: connectKindOf(value.connect),
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

/**
 * The non-protocol wires (legacy SSE `interrupt`, AG-UI `moldy.interrupt`, reconnect
 * synthesis) carry the raw value with its kind in `type`; the rest of the frontend only
 * handles standard payloads.
 */
export function toStandardInterruptPayload(data: InterruptPayload): StandardInterruptPayload {
  if (!('type' in data)) return data
  const empty = { interrupt_id: data.interrupt_id, action_requests: [], review_configs: [] }
  return nativeAuthRequiredPayload(data.interrupt_id, { ...data }) ?? empty
}

/** The card's answer (13.6): `{type: 'respond', message}` plus the attempt it answers (G-06). */
export function authRequiredDecision(
  response: AuthRequiredResponse,
  attempt: number | undefined,
): Decision {
  return {
    type: 'respond',
    message: response,
    ...(attempt !== undefined && { attempt }),
  }
}

function attemptOf(args: Record<string, unknown> | undefined): number | null {
  return typeof args?.attempt === 'number' ? args.attempt : null
}

/** The attempt of an `auth_required` payload (null for every other interrupt). */
export function authRequiredAttempt(payload: StandardInterruptPayload): number | null {
  return payload.native === 'auth_required' ? attemptOf(payload.action_requests[0]?.args) : null
}

/** Interrupt identity for dedup/resolution: the id, plus the attempt of an auth re-ask. */
export function interruptIdentity(payload: StandardInterruptPayload): string {
  const attempt = authRequiredAttempt(payload)
  return attempt === null ? payload.interrupt_id : `${payload.interrupt_id}#${attempt}`
}

/** Same identity, read back from a resolved synthetic tool call's args. */
export function resolvedIdentity(name: string, args: Record<string, unknown>): string {
  const id = String(args.hitl_interrupt_id ?? '')
  const attempt = name === AUTH_REQUIRED_ACTION ? attemptOf(args) : null
  return attempt === null ? id : `${id}#${attempt}`
}
