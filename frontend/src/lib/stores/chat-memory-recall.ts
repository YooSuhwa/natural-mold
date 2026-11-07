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

/** How a memory reached the prompt: profile document, automatic recall, or the recall tool. */
export type MemoryRecallPath = 'profile' | 'auto' | 'tool'

export interface RecalledMemoryBrief {
  /** Source memory id; absent for a profile line the person wrote (no source memory). */
  readonly id?: string
  /** Profile briefs only: the profile line the brief came from. */
  readonly lineId?: string
  readonly scope: 'user' | 'agent'
  readonly content: string
  /** Absent on briefs persisted before paths existed; read as `auto`. */
  readonly path?: MemoryRecallPath
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
