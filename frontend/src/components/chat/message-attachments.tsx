'use client'

import { useMemo, useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { EyeOffIcon, FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { ChatImage } from '@/components/chat/chat-image'
import { attachmentToArtifactSummary } from '@/lib/chat/attachment-to-artifact'
import { getAttachmentTextPreview } from '@/lib/chat/attachment-preview'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { useMoldyAgUiActivities } from '@/lib/ag-ui/activity-context'
import {
  findChatMessageLink,
  storedChatMessageId,
  wireChatMessageId,
} from '@/lib/ag-ui/chat-message-identity'
import { useConversationFiles } from '@/lib/hooks/use-conversation-files'
import type { ChatImageDescription, FileItem, MessageAttachmentBrief } from '@/lib/types'

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
      className="moldy-dialog-attachment-preview"
    >
      <DialogShell.Header title={<span className="truncate">{brief.filename}</span>} />
      <DialogShell.Body className="min-h-0 overflow-auto">
        {open ? (
          <ArtifactPreview
            artifact={attachmentToArtifactSummary(brief)}
            textLoader={() => getAttachmentTextPreview(brief.id)}
          />
        ) : null}
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

  const openLabel = tMessageArtifacts('openLabel', { name: brief.filename })
  if (brief.mime_type.startsWith('image/')) {
    return <ChatImage src={brief.url} alt={brief.filename} openLabel={openLabel} />
  }

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

const descriptionReasons = {
  policy_denied: 'policyDenied',
  unavailable: 'unavailable',
  size_limit: 'sizeLimit',
  budget: 'budget',
  timeout: 'timeout',
  invalid_image: 'invalidImage',
  provider_error: 'providerError',
  pending: 'pending',
  unconfirmed: 'unconfirmed',
} as const

function ImageDescription({ description }: { description: ChatImageDescription }) {
  const t = useTranslations('chat.message.artifacts.imageDescription')
  if (description.status === 'ready') {
    return (
      <div className="max-w-prose space-y-1 break-words text-left text-sm">
        <p className="font-medium">{t('heading')}</p>
        <p className="whitespace-pre-wrap">{description.text}</p>
      </div>
    )
  }

  const reason = Object.hasOwn(descriptionReasons, description.reason)
    ? descriptionReasons[description.reason as keyof typeof descriptionReasons]
    : 'failed'
  return <p className="moldy-ui-caption max-w-prose text-left text-muted-foreground">{t(reason)}</p>
}

/** 실행 기록상 모델에 전달되지 않은 첨부 형식(``attachment_form``). */
const NOT_SENT_REASONS = {
  image_no_vision: 'notSentNoVision',
  unsupported: 'notSentUnsupported',
} as const

/**
 * 모델에 전달되지 않은 첨부 아래에 붙는 작은 표시. 전달된 첨부(이미지·텍스트·PDF)와
 * 기록이 없는 첨부에는 아무것도 렌더하지 않는다.
 */
export function AttachmentModelInputNote({ modelInput }: { modelInput?: string | null }) {
  const t = useTranslations('chat.message.artifacts')
  const reason =
    modelInput && modelInput in NOT_SENT_REASONS
      ? NOT_SENT_REASONS[modelInput as keyof typeof NOT_SENT_REASONS]
      : null
  if (!reason) return null
  return (
    <span
      title={t(reason)}
      className="moldy-ui-caption inline-flex items-center gap-1 text-status-warn"
    >
      <EyeOffIcon className="size-3" aria-hidden />
      {t('notSent')}
      <span className="sr-only">: {t(reason)}</span>
    </span>
  )
}

/** Resolve previews and descriptions from the conversation's authorized file listing. */
export function UserMessageAttachments() {
  const conversationId = useChatConversationId()
  const activity = useMoldyAgUiActivities()
  // /files links an upload to its stored message UUID. Before link hydration
  // a usage row may also identify the precise turn by its original runtime ID.
  const messageId = useAuiState((s) => {
    const message = s.message
    if (message?.role !== 'user') return null
    if (!activity) return storedChatMessageId(message) ?? message.id
    if (activity.conversationId !== conversationId) return null
    const link = findChatMessageLink(message, activity.links)
    if (link) return link.uiMessageId ?? link.messageId
    const wire = wireChatMessageId(message)
    // A reused provider ID is insufficient to pick a sibling or a child row.
    if (activity.links.some((entry) => entry.messageId === wire)) return null
    return wire
  })
  const { data, error } = useConversationFiles(conversationId)
  const files = useMemo(() => {
    if (!messageId) return []
    return (data ?? []).flatMap((file) => {
      if (file.source !== 'attached') return []
      const uses = file.image_description_uses
      const exact = uses?.find((entry) => entry.message_id === messageId)
      const runtime = exact ? [] : uses?.filter((entry) => entry.runtime_message_id === messageId)
      const use = exact ?? (runtime?.length === 1 ? runtime[0] : undefined)
      if (!use && file.message_id !== messageId) return []
      // An upload can be reused on a fork with a different question.
      const description = uses === undefined ? file.image_description : use?.image_description
      return [{ file, description }]
    })
  }, [data, messageId])

  const status = error && 'status' in error ? error.status : null
  const lostAccess = typeof status === 'number' && [401, 403, 404, 410].includes(status)
  if (lostAccess || files.length === 0) return null
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {files.map(({ file, description }) => (
        <div key={file.id} className="inline-flex min-w-0 max-w-full flex-col items-end gap-0.5">
          <MessageAttachmentItem brief={fileItemToBrief(file)} />
          {!error && file.mime_type.startsWith('image/') && description ? (
            <ImageDescription description={description} />
          ) : (
            <AttachmentModelInputNote modelInput={file.model_input} />
          )}
        </div>
      ))}
    </div>
  )
}
