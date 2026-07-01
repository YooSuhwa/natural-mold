'use client'

import { DownloadIcon, FileJsonIcon, FileTextIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import {
  conversationToJson,
  conversationToMarkdown,
  downloadTextFile,
  exportFilename,
  type ExportLabels,
} from '@/lib/chat/conversation-export'
import { useMessagesEnvelope } from '@/lib/hooks/use-conversations'

interface ExportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: string
  title?: string | null
}

export function ExportDialog({ open, onOpenChange, conversationId, title }: ExportDialogProps) {
  const t = useTranslations('chat.export')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="md" height="auto">
      <DialogShell.Header
        icon={<DownloadIcon className="size-5" />}
        title={t('title')}
        description={t('description')}
      />
      {/* Body is inert when closed (DialogPortal unmounts), so it can fetch freely. */}
      {open ? (
        <ExportDialogBody
          conversationId={conversationId}
          title={title}
          onDone={() => onOpenChange(false)}
        />
      ) : null}
    </DialogShell>
  )
}

function fileTimestamp(): string {
  // ISO를 파일명 안전한 형태로 (콜론/점 제거). "2026-07-02T00-30-00".
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
}

function ExportDialogBody({
  conversationId,
  title,
  onDone,
}: {
  conversationId: string
  title?: string | null
  onDone: () => void
}) {
  const t = useTranslations('chat.export')
  const { data: envelope, isLoading } = useMessagesEnvelope(conversationId)

  const labels: ExportLabels = {
    roleUser: t('role.user'),
    roleAssistant: t('role.assistant'),
    roleTool: t('role.tool'),
    toolCalls: t('toolCalls'),
    attachments: t('attachments'),
    exportedAt: t('exportedAt'),
  }

  const messageCount = envelope?.messages.length ?? 0
  const disabled = isLoading || !envelope || messageCount === 0

  function handleMarkdown() {
    if (!envelope) return
    const content = conversationToMarkdown(envelope.messages, {
      title: title?.trim() || conversationId,
      exportedAt: new Date().toISOString(),
      labels,
    })
    downloadTextFile(
      content,
      exportFilename(conversationId, 'md', fileTimestamp()),
      'text/markdown',
    )
    toast.success(t('toast.exported'))
    onDone()
  }

  function handleJson() {
    if (!envelope) return
    const content = conversationToJson(envelope)
    downloadTextFile(
      content,
      exportFilename(conversationId, 'json', fileTimestamp()),
      'application/json',
    )
    toast.success(t('toast.exported'))
    onDone()
  }

  return (
    <DialogShell.Body>
      {isLoading ? (
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      ) : messageCount === 0 ? (
        <p className="text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{t('hint', { count: messageCount })}</p>
          <div className="flex flex-col gap-2">
            <Button
              variant="outline"
              onClick={handleMarkdown}
              disabled={disabled}
              className="justify-start"
            >
              <FileTextIcon className="size-4" />
              {t('formatMarkdown')}
            </Button>
            <Button
              variant="outline"
              onClick={handleJson}
              disabled={disabled}
              className="justify-start"
            >
              <FileJsonIcon className="size-4" />
              {t('formatJson')}
            </Button>
          </div>
        </div>
      )}
    </DialogShell.Body>
  )
}
