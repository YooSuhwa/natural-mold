'use client'

import { useMemo, useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { ChatImage } from '@/components/chat/chat-image'
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
 * 비이미지 첨부(PDF/문서/텍스트…)의 미리보기 다이얼로그.
 *
 * 이미지가 여는 ``ChatImage`` 라이트박스와 **같은 풀스크린 ``DialogShell`` 껍데기**를
 * 써서 두 뷰어가 시각적으로 일관되게 보이게 한다(내용은 ``ArtifactPreview``로 타입별
 * 렌더 — 미지원은 다운로드 fallback). ``open``일 때만 마운트해 불필요한 fetch를 막는다.
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
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      size="xl"
      height="auto"
      className="!h-[calc(100vh-2rem)] !max-h-[calc(100vh-2rem)] !w-[calc(100vw-2rem)] !max-w-[calc(100vw-2rem)] lg:!w-[min(calc(100vw-2rem),1200px)]"
    >
      <DialogShell.Header title={<span className="truncate">{brief.filename}</span>} />
      <DialogShell.Body className="min-h-0 overflow-auto">
        {open ? <ArtifactPreview artifact={attachmentToArtifactSummary(brief)} /> : null}
      </DialogShell.Body>
    </DialogShell>
  )
}

/**
 * 보낸 메시지 버블에 표시되는 첨부 1개.
 * - 이미지: 채팅 공용 ``ChatImage`` 재사용 → 마크다운/인라인 이미지와 동일한
 *   썸네일 + 클릭 시 풀스크린 라이트박스(일관 UX).
 * - 그 외(PDF/문서/텍스트): 파일 칩 → ``ArtifactPreview`` 다이얼로그(미지원은 다운로드 fallback).
 * 보낸 첨부이므로 읽기 전용(제거/수정 없음).
 */
export function MessageAttachmentItem({ brief }: { brief: MessageAttachmentBrief }) {
  const tMessageArtifacts = useTranslations('chat.message.artifacts')
  const [open, setOpen] = useState(false)

  if (brief.mime_type.startsWith('image/')) {
    return <ChatImage src={brief.url} alt={brief.filename} />
  }

  const openLabel = tMessageArtifacts('openLabel', { name: brief.filename })
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={openLabel}
        title={brief.filename}
        className="moldy-card-hover inline-flex max-w-56 items-center gap-2 rounded-lg border border-border bg-muted/40 px-2.5 py-2 text-left transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <FileIcon className="size-3.5" />
        </span>
        <span className="min-w-0 truncate text-xs text-foreground">{brief.filename}</span>
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
