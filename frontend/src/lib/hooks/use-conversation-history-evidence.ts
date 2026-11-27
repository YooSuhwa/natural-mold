'use client'

import { useEffect, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import {
  readCompactionEvidence,
  resolveCompactionCheckpoint,
  type CompactionEvidenceTarget,
  type EvidenceCursor,
} from '@/lib/api/conversation-history-evidence'
import {
  MOLDY_BRANCH_SWITCHED_EVENT,
  isMoldyBranchSwitchedEvent,
} from '@/lib/chat/langgraph-runtime/branch-switch-events'
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

interface DisclosureRead {
  readonly controller: AbortController
  readonly expectedCheckpoint?: string
  readonly resolvedCheckpoint?: string
  readonly failed?: boolean
}

export function useCompactionDisclosure(evidence?: CompactionEvidenceTarget) {
  const [read, setRead] = useState<DisclosureRead | null>(null)
  const checkpoint = evidence?.checkpointId
  const conversationId = evidence?.conversationId
  const controller = read?.controller

  // A live message can gain its saved checkpoint while its first lookup is pending.
  // A different committed checkpoint always requires another user click.
  if (read) {
    const differentResult = Boolean(
      read.resolvedCheckpoint && checkpoint && read.resolvedCheckpoint !== checkpoint,
    )
    if (differentResult || (read.expectedCheckpoint && read.expectedCheckpoint !== checkpoint)) {
      setRead(null)
    } else if (checkpoint && !read.expectedCheckpoint) {
      setRead({ ...read, expectedCheckpoint: checkpoint })
    }
  }

  useEffect(() => () => controller?.abort(), [controller])
  useEffect(() => {
    if (!controller) return
    const closeOnBranchSwitch = (event: Event) => {
      if (isMoldyBranchSwitchedEvent(event) && event.detail.conversationId === conversationId) {
        controller.abort()
        setRead(null)
      }
    }
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, closeOnBranchSwitch)
    return () => window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, closeOnBranchSwitch)
  }, [controller, conversationId])

  function toggle() {
    if (!evidence || read) {
      setRead(null)
      return
    }
    const attempt = new AbortController()
    setRead({ controller: attempt, expectedCheckpoint: checkpoint, resolvedCheckpoint: checkpoint })
    if (checkpoint) return
    void resolveCompactionCheckpoint(evidence, attempt.signal).then(
      (resolvedCheckpoint) => {
        setRead((current) =>
          current?.controller === attempt ? { ...current, resolvedCheckpoint } : current,
        )
      },
      () => {
        if (!attempt.signal.aborted) {
          setRead((current) =>
            current?.controller === attempt ? { ...current, failed: true } : current,
          )
        }
      },
    )
  }

  return {
    expanded: read !== null,
    checkpointId: read?.resolvedCheckpoint,
    unavailable: read?.failed === true,
    toggle,
  }
}
