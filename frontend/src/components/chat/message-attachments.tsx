'use client'

/* eslint-disable @next/next/no-img-element */

import { useState } from 'react'
import { MessagePrimitive, useAuiState } from '@assistant-ui/react'
import { FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn, resolveImageUrl } from '@/lib/utils'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { attachmentToArtifactSummary } from '@/lib/chat/attachment-to-artifact'
import type { MessageAttachmentBrief } from '@/lib/types'

/**
 * assistant-ui ``CompleteAttachment`` + convert-message가 보존한 비표준 메타
 * (``url``/``size_bytes``)를 함께 읽기 위한 런타임 형태. 모두 optional/unknown으로
 * 두고 ``briefFromAttachment``에서 가드한다.
 */
interface MessageAttachmentLike {
  id?: unknown
  name?: unknown
  contentType?: unknown
  url?: unknown
  size_bytes?: unknown
  content?: ReadonlyArray<{ type?: string; image?: unknown }>
}

/** 이미지 첨부는 IMAGE 파트(content[0].image)에 업로드 URL을 담는다. */
function urlFromContent(content: MessageAttachmentLike['content']): string | null {
  const first = content?.[0]
  if (first?.type === 'image' && typeof first.image === 'string' && first.image.length > 0) {
    return first.image
  }
  return null
}

/**
 * assistant-ui attachment 객체 → ``MessageAttachmentBrief`` 재구성.
 * id/url을 복원하지 못하면(미리보기를 열 수 없으므로) null을 반환한다.
 */
export function briefFromAttachment(attachment: unknown): MessageAttachmentBrief | null {
  if (!attachment || typeof attachment !== 'object') return null
  const att = attachment as MessageAttachmentLike
  if (typeof att.id !== 'string' || att.id.length === 0) return null
  const url =
    typeof att.url === 'string' && att.url.length > 0 ? att.url : urlFromContent(att.content)
  if (!url) return null
  return {
    id: att.id,
    filename: typeof att.name === 'string' && att.name.length > 0 ? att.name : 'file',
    mime_type: typeof att.contentType === 'string' ? att.contentType : '',
    size_bytes: typeof att.size_bytes === 'number' ? att.size_bytes : 0,
    url,
  }
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
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="truncate pr-8">{brief.filename}</DialogTitle>
          </DialogHeader>
          {open ? <ArtifactPreview artifact={attachmentToArtifactSummary(brief)} /> : null}
        </DialogContent>
      </Dialog>
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
  const count = useAuiState((s) =>
    s.message?.role === 'user' ? (s.message.attachments?.length ?? 0) : 0,
  )
  if (count === 0) return null
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      <MessagePrimitive.Attachments>
        {({ attachment }) => {
          const brief = briefFromAttachment(attachment)
          if (!brief) return null
          return <MessageAttachmentItem brief={brief} />
        }}
      </MessagePrimitive.Attachments>
    </div>
  )
}
