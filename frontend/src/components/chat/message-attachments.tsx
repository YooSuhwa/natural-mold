'use client'

/* eslint-disable @next/next/no-img-element */

import { useMemo, useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn, resolveImageUrl } from '@/lib/utils'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { attachmentToArtifactSummary } from '@/lib/chat/attachment-to-artifact'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { useConversationFiles } from '@/lib/hooks/use-conversation-files'
import type { FileItem, MessageAttachmentBrief } from '@/lib/types'

/** Unified-files attachment row → the brief the preview cards consume. */
export function fileItemToBrief(file: FileItem): MessageAttachmentBrief {
  return {
    id: file.id,
    filename: file.name,
    mime_type: file.mime_type,
    size_bytes: file.size_bytes ?? 0,
    url: file.preview_url,
  }
}

/**
 * 첨부 1개의 미리보기 다이얼로그(제어형). 보낸 메시지 버블과 우측 레일의 첨부
 * 카드가 동일한 미리보기 경로(``ArtifactPreview``)를 공유하기 위해 분리했다.
 * ``open``일 때만 ``ArtifactPreview``를 마운트해 불필요한 text fetch를 막는다.
 */
export function AttachmentPreviewDialog({
  brief,
  open,
  onOpenChange,
}: {
  brief: MessageAttachmentBrief
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-8">{brief.filename}</DialogTitle>
        </DialogHeader>
        {open ? <ArtifactPreview artifact={attachmentToArtifactSummary(brief)} /> : null}
      </DialogContent>
    </Dialog>
  )
}

/**
 * 보낸 메시지 버블에 표시되는 첨부 1개. 이미지는 썸네일, 그 외는 파일 칩.
 * 클릭하면 기존 artifact 미리보기(``ArtifactPreview``)를 다이얼로그로 연다.
 * 보낸 첨부이므로 읽기 전용(제거/수정 없음).
 */
export function MessageAttachmentItem({ brief }: { brief: MessageAttachmentBrief }) {
  const tMessageArtifacts = useTranslations('chat.message.artifacts')
  const [open, setOpen] = useState(false)
  const isImage = brief.mime_type.startsWith('image/')
  const src = resolveImageUrl(brief.url) ?? brief.url
  const openLabel = tMessageArtifacts('openLabel', { name: brief.filename })

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={openLabel}
        title={brief.filename}
        className={cn(
          'moldy-card-hover overflow-hidden rounded-lg border border-border bg-muted/40 text-left transition-colors',
          'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
          isImage ? 'size-20' : 'inline-flex max-w-56 items-center gap-2 px-2.5 py-2',
        )}
      >
        {isImage ? (
          <img src={src} alt={brief.filename} className="size-full object-cover" />
        ) : (
          <>
            <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
              <FileIcon className="size-3.5" />
            </span>
            <span className="min-w-0 truncate text-xs text-foreground">{brief.filename}</span>
          </>
        )}
      </button>
      <AttachmentPreviewDialog brief={brief} open={open} onOpenChange={setOpen} />
    </>
  )
}

/**
 * 보낸 user 메시지 버블의 첨부 행. assistant-ui ``MessagePrimitive.Attachments``
 * render-prop으로 각 첨부를 순회한다. 첨부가 없으면 아무것도 렌더하지 않는다.
 *
 * 무한 렌더 가드: count selector는 reference-stable한 숫자만 반환한다.
 */
export function UserMessageAttachments() {
  // The v3 runtime builds messages from LangGraph state (LangChain messages),
  // which do NOT carry the moldy attachment side channel — so `s.message`
  // never exposes `attachments`. Instead we key off the message id (which
  // equals the backfilled `message_attachments.message_id` — same id the
  // anchor/jump uses) and look this turn's attachments up from the unified
  // `/files` list. Reference-stable selector (a string id) avoids re-render loops.
  const conversationId = useChatConversationId()
  const messageId = useAuiState((s) => (s.message?.role === 'user' ? s.message.id : null))
  const { data } = useConversationFiles(conversationId)
  const briefs = useMemo<MessageAttachmentBrief[]>(() => {
    if (!messageId) return []
    return (data ?? [])
      .filter((f) => f.source === 'attached' && f.message_id === messageId)
      .map(fileItemToBrief)
  }, [data, messageId])

  if (briefs.length === 0) return null
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {briefs.map((brief) => (
        <MessageAttachmentItem key={brief.id} brief={brief} />
      ))}
    </div>
  )
}
