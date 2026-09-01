'use client'

import { AttachmentPrimitive, useAuiState } from '@assistant-ui/react'
import { FileIcon, ImageIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

export function AttachmentChip() {
  const tMsg = useTranslations('chat.message')
  const attachment = useAuiState(
    (state) =>
      (
        state as {
          attachment?: { name: string; contentType?: string; status?: { type: string } }
        }
      ).attachment,
  )
  if (!attachment) return null
  const isImage = attachment.contentType?.startsWith('image/')
  const isUploading = attachment.status?.type === 'running'
  return (
    <AttachmentPrimitive.Root className="m-1 inline-flex min-w-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1 text-xs">
      <span className="flex size-5 shrink-0 items-center justify-center text-muted-foreground">
        {isImage ? <ImageIcon className="size-3.5" /> : <FileIcon className="size-3.5" />}
      </span>
      <span className="max-w-48 truncate">
        <AttachmentPrimitive.Name />
      </span>
      {isUploading && (
        <span className="moldy-ui-micro text-muted-foreground">{tMsg('attachmentUploading')}</span>
      )}
      <AttachmentPrimitive.Remove asChild>
        <button
          type="button"
          className="ml-1 inline-flex size-4 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label={tMsg('attachmentRemove')}
        >
          <XIcon className="size-3" />
        </button>
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
  )
}
