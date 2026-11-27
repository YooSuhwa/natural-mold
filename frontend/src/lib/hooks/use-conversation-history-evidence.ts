'use client'

import { useInfiniteQuery } from '@tanstack/react-query'
import {
  readCompactionEvidence,
  type CompactionEvidenceTarget,
  type EvidenceCursor,
} from '@/lib/api/conversation-history-evidence'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'

export type { CompactionEvidenceTarget }

export function useConversationHistoryEvidence(target: CompactionEvidenceTarget) {
  const first: EvidenceCursor = { offset: 0 }
  return useInfiniteQuery({
    queryKey: conversationQueryKeys.historyEvidence(
      target.conversationId,
      target.messageId,
      target.historyId,
      target.checkpointId,
      target.cutoffIndex,
    ),
    initialPageParam: first,
    queryFn: ({ pageParam, signal }) => readCompactionEvidence(target, pageParam, signal),
    getNextPageParam: (last): EvidenceCursor | undefined =>
      last.next_offset === null
        ? undefined
        : {
            offset: last.next_offset,
            checkpointId: last.checkpoint_id,
            digest: last.content_sha256,
          },
    gcTime: 0,
    retry: false,
  })
}
