import { atom } from 'jotai'

/**
 * Conversation-scoped long-term memory recall briefs.
 *
 * The backend injects up to N memory records into each run's system prompt and
 * ships the briefs once per run over a custom `moldy.memory_recalled` event
 * (see `memory-recall-events.ts`). The chat renders them as a "기억 N개 참고"
 * chip. Later runs REPLACE the conversation entry (recall is recomputed per
 * run), and replay on reload re-populates in event order so the last run wins.
 */

export interface RecalledMemoryBrief {
  readonly id?: string
  readonly scope: 'user' | 'agent'
  readonly content: string
}

export type ChatMemoryRecallState = Record<string, readonly RecalledMemoryBrief[]>

export const chatMemoryRecallAtom = atom<ChatMemoryRecallState>({})

export const setConversationMemoryRecallAtom = atom(
  null,
  (get, set, payload: { conversationId: string; memories: readonly RecalledMemoryBrief[] }) => {
    const current = get(chatMemoryRecallAtom)
    set(chatMemoryRecallAtom, {
      ...current,
      [payload.conversationId]: payload.memories,
    })
  },
)
