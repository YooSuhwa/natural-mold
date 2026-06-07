import { atom } from 'jotai'
import type { ArtifactSummary, FileEventPayload } from '@/lib/types'

export interface ConversationArtifactState {
  items: ArtifactSummary[]
  selectedArtifactId: string | null
}

export type ChatArtifactsState = Record<string, ConversationArtifactState>

function sortArtifacts(items: ArtifactSummary[]): ArtifactSummary[] {
  return [...items].sort((left, right) => {
    if (left.run_id !== right.run_id) return right.created_at.localeCompare(left.created_at)
    return left.path.localeCompare(right.path)
  })
}

export function upsertArtifactList(
  items: readonly ArtifactSummary[],
  event: FileEventPayload,
): ArtifactSummary[] {
  if (event.op === 'deleted') return items.filter((item) => item.id !== event.id)
  const { op: _op, ...nextItem } = event
  void _op
  const existingIndex = items.findIndex((item) => item.id === event.id)
  if (existingIndex === -1) return sortArtifacts([...items, nextItem])
  const next = [...items]
  next[existingIndex] = { ...next[existingIndex], ...nextItem }
  return sortArtifacts(next)
}

function nextSelectedArtifactId(
  previous: ConversationArtifactState,
  items: ArtifactSummary[],
  event: FileEventPayload,
): string | null {
  if (event.op !== 'deleted') return event.id
  if (previous.selectedArtifactId === event.id) return items[0]?.id ?? null
  if (
    previous.selectedArtifactId &&
    items.some((item) => item.id === previous.selectedArtifactId)
  ) {
    return previous.selectedArtifactId
  }
  return items[0]?.id ?? null
}

export const chatArtifactsAtom = atom<ChatArtifactsState>({})

export const upsertChatArtifactAtom = atom(null, (get, set, event: FileEventPayload) => {
  const current = get(chatArtifactsAtom)
  const conversationId = event.conversation_id
  const previous = current[conversationId] ?? { items: [], selectedArtifactId: null }
  const items = upsertArtifactList(previous.items, event)
  set(chatArtifactsAtom, {
    ...current,
    [conversationId]: {
      items,
      selectedArtifactId: nextSelectedArtifactId(previous, items, event),
    },
  })
})

export const setConversationArtifactsAtom = atom(
  null,
  (
    get,
    set,
    payload: {
      conversationId: string
      items: ArtifactSummary[]
      selectedArtifactId?: string | null
    },
  ) => {
    const current = get(chatArtifactsAtom)
    const previous = current[payload.conversationId]
    const items = sortArtifacts(payload.items)
    const selectedArtifactId =
      payload.selectedArtifactId ??
      (previous?.selectedArtifactId && items.some((item) => item.id === previous.selectedArtifactId)
        ? previous.selectedArtifactId
        : (items[0]?.id ?? null))
    set(chatArtifactsAtom, {
      ...current,
      [payload.conversationId]: {
        items,
        selectedArtifactId,
      },
    })
  },
)

export const selectChatArtifactAtom = atom(
  null,
  (
    get,
    set,
    payload: {
      conversationId: string
      artifactId: string | null
    },
  ) => {
    const current = get(chatArtifactsAtom)
    const previous = current[payload.conversationId] ?? { items: [], selectedArtifactId: null }
    set(chatArtifactsAtom, {
      ...current,
      [payload.conversationId]: {
        ...previous,
        selectedArtifactId: payload.artifactId,
      },
    })
  },
)
