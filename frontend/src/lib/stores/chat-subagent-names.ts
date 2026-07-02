import { atom } from 'jotai'

/**
 * Conversation-scoped subagent runtime_name -> display_name map.
 *
 * The v3 stream carries deepagents `task` tool calls whose `subagent_type` is the
 * runtime name (`agent_<8hex>`); the SDK uses it verbatim as the subagent card
 * title. The backend ships the human-readable map once per run over a custom
 * `moldy.subagent_names` event (see `subagent-names-events.ts`), and the card /
 * right rail substitute the display name at render time only — the SDK snapshot
 * (and the checkpoint-backed `subagent_type`) stay untouched.
 *
 * Keyed by conversationId so distinct threads never bleed. Merges are additive
 * and idempotent, so replay/re-entry safely re-applies the same mapping.
 */
export type ChatSubagentNamesState = Record<string, Record<string, string>>

export const chatSubagentNamesAtom = atom<ChatSubagentNamesState>({})

export const mergeConversationSubagentNamesAtom = atom(
  null,
  (get, set, payload: { conversationId: string; names: Record<string, string> }) => {
    const current = get(chatSubagentNamesAtom)
    const previous = current[payload.conversationId] ?? {}
    set(chatSubagentNamesAtom, {
      ...current,
      [payload.conversationId]: { ...previous, ...payload.names },
    })
  },
)

/** Resolve a runtime_name to its display name, falling back to the raw name. */
export function resolveSubagentDisplayName(
  names: Record<string, string> | undefined,
  rawName: string,
): string {
  return names?.[rawName] ?? rawName
}
