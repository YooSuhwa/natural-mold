import { apiFetch } from '@/lib/api/client'
import { loadServerCheckpointContext } from '@/lib/chat/langgraph-runtime/thread-state-checkpoints'

export type CompactionEvidenceTarget = {
  readonly conversationId: string
  readonly historyId: string
  readonly cutoffIndex?: number
} & (
  | { readonly messageId: string; readonly checkpointId?: string }
  | { readonly checkpointId: string; readonly messageId?: never }
)

export interface EvidenceCursor {
  readonly offset: number
  readonly checkpointId?: string
  readonly digest?: string
}

interface HistoryEvidence {
  readonly checkpoint_id: string
  readonly history_id: string
  readonly cutoff_index: number | null
  readonly content_sha256: string
  readonly excerpts: readonly {
    readonly role: 'user' | 'assistant'
    readonly text: string
    readonly truncated: boolean
  }[]
  readonly next_offset: number | null
}

export async function readCompactionEvidence(
  target: CompactionEvidenceTarget,
  cursor: EvidenceCursor,
  signal: AbortSignal,
): Promise<HistoryEvidence> {
  const checkpointId =
    cursor.checkpointId ??
    target.checkpointId ??
    (target.messageId
      ? (await loadServerCheckpointContext(target.conversationId)).checkpointByMessageId.get(
          target.messageId,
        )
      : undefined)
  signal.throwIfAborted()
  if (!checkpointId) throw new Error('history_evidence_unavailable')

  const base = `/api/conversations/${encodeURIComponent(target.conversationId)}/history-evidence`
  const params = new URLSearchParams({ offset: String(cursor.offset), limit: '10' })
  if (cursor.digest) params.set('expected_sha256', cursor.digest)
  const page = await apiFetch<HistoryEvidence>(
    `${base}/${encodeURIComponent(checkpointId)}?${params}`,
    {
      signal,
      cache: 'no-store',
    },
  )
  if (
    page.checkpoint_id !== checkpointId ||
    page.history_id !== target.historyId ||
    (target.cutoffIndex !== undefined && page.cutoff_index !== target.cutoffIndex) ||
    !/^[0-9a-f]{64}$/.test(page.content_sha256) ||
    (cursor.digest && page.content_sha256 !== cursor.digest) ||
    (page.next_offset !== null &&
      (!Number.isSafeInteger(page.next_offset) || page.next_offset <= cursor.offset))
  ) {
    throw new Error('history_evidence_unavailable')
  }
  return page
}
