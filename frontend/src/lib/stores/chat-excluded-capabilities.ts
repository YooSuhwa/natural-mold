import { atom } from 'jotai'

/**
 * F-06 / plan 13.6 — capabilities the run's access plan left out, per conversation.
 *
 * The backend ships `moldy.excluded_capabilities` once at run start (v3 custom channel,
 * legacy SSE event) with per-(type, reason) counts only — never resource ids — and only
 * when something was excluded. So every run start (v3 `running`, legacy `message_start`)
 * clears the notice: a later clean run hides the banner. Runs are remembered in arrival
 * order; a replay of an older run (reload/reconnect) never resurrects its notice over a
 * newer run, and a replay of the latest run keeps the user's dismissal.
 */

export type ExcludedCapabilityReason = 'broken' | 'denied'

export interface ExcludedCapabilityGroup {
  readonly type: string
  readonly reason: ExcludedCapabilityReason
  readonly count: number
}

export interface ExcludedCapabilitiesNotice {
  readonly runKey: string
  readonly groups: readonly ExcludedCapabilityGroup[]
  readonly dismissed: boolean
}

export interface ConversationExclusions {
  /** Run ids seen in arrival order (bounded). */
  readonly runs: readonly string[]
  readonly notice: ExcludedCapabilitiesNotice | null
}

const MAX_RUNS = 50

/** Legacy panels without a conversation (assistant panel, test chat) share one slot. */
export const NO_CONVERSATION_SCOPE = '__no_conversation__'

export function excludedCapabilitiesScope(conversationId: string | null | undefined): string {
  return conversationId || NO_CONVERSATION_SCOPE
}

export const chatExcludedCapabilitiesAtom = atom<Record<string, ConversationExclusions>>({})

const EMPTY: ConversationExclusions = { runs: [], notice: null }

function withRun(entry: ConversationExclusions, runId: string): ConversationExclusions {
  return { runs: [...entry.runs, runId].slice(-MAX_RUNS), notice: null }
}

/** A run started: a new run clears the notice; a replayed (already seen) run is a no-op. */
export const startExcludedCapabilitiesRunAtom = atom(
  null,
  (get, set, payload: { conversationId: string | null | undefined; runId: string }) => {
    const scope = excludedCapabilitiesScope(payload.conversationId)
    const current = get(chatExcludedCapabilitiesAtom)
    const entry = current[scope] ?? EMPTY
    if (entry.runs.includes(payload.runId)) return
    set(chatExcludedCapabilitiesAtom, { ...current, [scope]: withRun(entry, payload.runId) })
  },
)

export const setExcludedCapabilitiesAtom = atom(
  null,
  (
    get,
    set,
    payload: {
      conversationId: string | null | undefined
      /** The run the event belongs to; `undefined` = the latest run seen. */
      runId: string | undefined
      groups: readonly ExcludedCapabilityGroup[]
    },
  ) => {
    const scope = excludedCapabilitiesScope(payload.conversationId)
    const current = get(chatExcludedCapabilitiesAtom)
    let entry = current[scope] ?? EMPTY
    const latest = entry.runs.at(-1)
    const runId = payload.runId ?? latest ?? 'unknown-run'
    if (entry.runs.includes(runId) && runId !== latest) return // older run's replay
    if (!entry.runs.includes(runId)) entry = withRun(entry, runId)
    const previous = entry.notice
    const dismissed = previous?.runKey === runId ? previous.dismissed : false
    set(chatExcludedCapabilitiesAtom, {
      ...current,
      [scope]: { ...entry, notice: { runKey: runId, groups: payload.groups, dismissed } },
    })
  },
)

export const dismissExcludedCapabilitiesAtom = atom(
  null,
  (get, set, conversationId: string | null | undefined) => {
    const scope = excludedCapabilitiesScope(conversationId)
    const current = get(chatExcludedCapabilitiesAtom)
    const entry = current[scope]
    if (!entry?.notice || entry.notice.dismissed) return
    set(chatExcludedCapabilitiesAtom, {
      ...current,
      [scope]: { ...entry, notice: { ...entry.notice, dismissed: true } },
    })
  },
)
