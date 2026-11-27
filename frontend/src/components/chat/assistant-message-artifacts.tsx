'use client'

import { useAuiState } from '@assistant-ui/react'
import { FileIcon, ImageIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtomValue, useSetAtom } from 'jotai'
import { CompactionSummary } from '@/components/chat/compaction-summary'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { selectMessageArtifactsFromMessage } from '@/components/chat/message-artifact-metadata'
import { useRecordArtifactOpened } from '@/lib/hooks/use-artifact-library'
import { selectChatArtifactAtom } from '@/lib/stores/chat-artifacts'
import {
  chatRightRailAtom,
  isArtifactPreviewOpen,
  toggleArtifactPreviewRailState,
} from '@/lib/stores/chat-right-rail'
import { sourceMessageIdFromThreadMessageId } from '@/lib/chat/langgraph-runtime/message-list'
import type { ArtifactSummary } from '@/lib/types'
import { isRecord } from '@/lib/chat/langgraph-runtime/usage-normalization'

function useMessageArtifacts(): readonly ArtifactSummary[] {
  return useAuiState((s) => selectMessageArtifactsFromMessage(s.message))
}

export function AssistantCompactionMarker() {
  const conversationId = useChatConversationId()
  const messageId = useAuiState((s) => sourceMessageIdFromThreadMessageId(s.message?.id))
  const metadata = useAuiState((s) => s.message?.metadata?.custom)
  const compaction = metadata?.compaction
  if (!isRecord(compaction)) return null
  const historyId = compaction.historyId
  const cutoff = compaction.cutoffIndex

  const source = metadata?.compactionMessageId
  const checkpoint = metadata?.checkpoint_id
  const evidence =
    conversationId &&
    typeof source === 'string' &&
    source.length > 0 &&
    typeof historyId === 'string' &&
    /^history_[a-f0-9]{24}$/.test(historyId)
      ? {
          conversationId,
          messageId: source,
          historyId,
          cutoffIndex:
            typeof cutoff === 'number' && Number.isSafeInteger(cutoff) ? cutoff : undefined,
          checkpointId:
            source === messageId &&
            typeof checkpoint === 'string' &&
            !checkpoint.startsWith('pending-') &&
            /^[A-Za-z0-9_-]{1,64}$/.test(checkpoint)
              ? checkpoint
              : undefined,
        }
      : undefined
  return <CompactionSummary className="mt-2" evidence={evidence} />
}

export function AssistantArtifactCards() {
  const artifacts = useMessageArtifacts()
  const conversationId = useChatConversationId()
  const selectArtifact = useSetAtom(selectChatArtifactAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const rightRail = useAtomValue(chatRightRailAtom)
  const openedMutation = useRecordArtifactOpened()
  const tArtifacts = useTranslations('chat.rightRail.artifacts')
  const tMessageArtifacts = useTranslations('chat.message.artifacts')

  if (artifacts.length === 0) return null

  const openArtifact = (artifact: ArtifactSummary) => {
    const targetConversationId = conversationId ?? artifact.conversation_id
    const nextState = toggleArtifactPreviewRailState(rightRail, {
      conversationId: targetConversationId,
      artifactId: artifact.id,
    })
    if (!isArtifactPreviewOpen(rightRail, targetConversationId, artifact.id)) {
      selectArtifact({ conversationId: targetConversationId, artifactId: artifact.id })
      openedMutation.mutate(artifact.id)
    }
    setRightRail(nextState)
  }

  return (
    <div className="mt-2 flex max-w-xl flex-col gap-2">
      {artifacts.map((artifact) => {
        const isImage = artifact.artifact_kind === 'image'
        const extension = artifact.extension?.toUpperCase()
        return (
          <button
            key={artifact.id}
            type="button"
            className="moldy-chat-card moldy-card-hover flex w-full items-center gap-3 px-3 py-3 text-left"
            aria-label={tMessageArtifacts('openLabel', { name: artifact.display_name })}
            onClick={() => openArtifact(artifact)}
          >
            <span className="flex size-12 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-foreground">
              {isImage ? <ImageIcon className="size-5" /> : <FileIcon className="size-5" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-foreground">
                {artifact.display_name}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {tArtifacts(`kinds.${artifact.artifact_kind}`)}
                {extension ? ` · ${extension}` : ''}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
