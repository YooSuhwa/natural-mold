'use client'

import { useMemo, useState } from 'react'
import { useAtomValue, useSetAtom } from 'jotai'
import { DownloadIcon, FileIcon, ImageIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { AttachmentPreviewDialog } from '@/components/chat/message-attachments'
import { JumpToMessageButton } from '@/components/chat/right-rail/jump-to-message'
import { useConversationArtifacts } from '@/lib/hooks/use-conversation-artifacts'
import { useConversationFiles } from '@/lib/hooks/use-conversation-files'
import { useRecordArtifactOpened } from '@/lib/hooks/use-artifact-library'
import { chatArtifactsAtom, selectChatArtifactAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom, type ArtifactsPayload } from '@/lib/stores/chat-right-rail'
import { openExternalUrl } from '@/lib/browser/window-open'
import type { ArtifactSummary, FileItem, MessageAttachmentBrief } from '@/lib/types'
import { cn, resolveImageUrl } from '@/lib/utils'
import { formatDisplayBytes } from '@/lib/utils/display-format'

interface Props {
  payload: ArtifactsPayload
}

function groupByRun(items: ArtifactSummary[]): Array<{ runId: string; items: ArtifactSummary[] }> {
  const groups = new Map<string, ArtifactSummary[]>()
  for (const item of items) {
    const group = groups.get(item.run_id) ?? []
    group.push(item)
    groups.set(item.run_id, group)
  }
  return Array.from(groups, ([runId, group]) => ({ runId, items: group }))
}

function selectedArtifactForPayload(
  items: ArtifactSummary[],
  payload: ArtifactsPayload,
  storeSelectedArtifactId: string | null | undefined,
): ArtifactSummary | null {
  const selectedIds = [payload.selectedArtifactId, storeSelectedArtifactId]
  for (const selectedId of selectedIds) {
    const artifact = items.find((item) => item.id === selectedId)
    if (artifact) return artifact
  }
  return items[0] ?? null
}

/** 생성 산출물 1개 행 — 클릭 시 우측 레일 미리보기 페인으로 전환(기존 동작 유지). */
function GeneratedFileRow({
  artifact,
  active,
  onSelect,
}: {
  artifact: ArtifactSummary
  active: boolean
  onSelect: (artifact: ArtifactSummary) => void
}) {
  const t = useTranslations('chat.rightRail.artifacts')
  const tFiles = useTranslations('chat.files')
  return (
    <div
      className={cn(
        'moldy-chat-card moldy-card-hover flex w-full items-center gap-2 px-3 py-3 text-sm',
        active && 'border-primary bg-accent',
      )}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 text-left focus-visible:outline-hidden"
        onClick={() => onSelect(artifact)}
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
          <FileIcon className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-foreground">
            {artifact.display_name}
          </span>
          <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Badge variant="secondary" className="shrink-0">
              {tFiles('generatedBadge')}
            </Badge>
            <span className="truncate">
              {t(`kinds.${artifact.artifact_kind}`)} ·{' '}
              {artifact.extension?.toUpperCase() ?? formatDisplayBytes(artifact.size_bytes)}
            </span>
          </span>
        </span>
      </button>
      <Badge variant="outline" className="shrink-0">
        {formatDisplayBytes(artifact.size_bytes)}
      </Badge>
      <JumpToMessageButton
        messageId={artifact.linked_message_ids?.[0] ?? artifact.assistant_msg_id}
      />
    </div>
  )
}

/** 사용자 첨부 1개 카드 — 읽기 전용(제거/수정 없음). 클릭 시 commit-5 미리보기 다이얼로그. */
function AttachedFileCard({ file }: { file: FileItem }) {
  const t = useTranslations('chat.rightRail.artifacts')
  const tFiles = useTranslations('chat.files')
  const tMessageArtifacts = useTranslations('chat.message.artifacts')
  const [open, setOpen] = useState(false)
  const isImage = file.mime_type.startsWith('image/')
  const downloadHref = resolveImageUrl(file.download_url) ?? file.download_url
  const brief: MessageAttachmentBrief = {
    id: file.id,
    filename: file.name,
    mime_type: file.mime_type,
    size_bytes: file.size_bytes ?? 0,
    url: file.preview_url,
  }
  return (
    <div className="moldy-chat-card moldy-card-hover flex w-full items-center gap-2 px-3 py-3 text-sm">
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 text-left focus-visible:outline-hidden"
        aria-label={tMessageArtifacts('openLabel', { name: file.name })}
        onClick={() => setOpen(true)}
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
          {isImage ? <ImageIcon className="size-5" /> : <FileIcon className="size-5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium text-foreground">{file.name}</span>
          <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Badge variant="outline" className="shrink-0">
              {tFiles('attachedBadge')}
            </Badge>
            <span className="truncate">
              {file.extension?.toUpperCase() ?? formatDisplayBytes(file.size_bytes ?? 0)}
            </span>
          </span>
        </span>
      </button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="shrink-0 text-muted-foreground"
        aria-label={t('download')}
        render={<a href={downloadHref} download={file.name} />}
      >
        <DownloadIcon className="size-4" />
      </Button>
      <JumpToMessageButton messageId={file.message_id} />
      <AttachmentPreviewDialog brief={brief} open={open} onOpenChange={setOpen} />
    </div>
  )
}

export function ArtifactPanelContent({ payload }: Props) {
  const t = useTranslations('chat.rightRail.artifacts')
  const tFiles = useTranslations('chat.files')
  const { isLoading } = useConversationArtifacts(payload.conversationId)
  const filesQuery = useConversationFiles(payload.conversationId)
  const state = useAtomValue(chatArtifactsAtom)[payload.conversationId]
  const selectArtifact = useSetAtom(selectChatArtifactAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const openedMutation = useRecordArtifactOpened()
  const items = useMemo(() => state?.items ?? [], [state?.items])
  const attachedFiles = useMemo(
    () => (filesQuery.data ?? []).filter((file) => file.source === 'attached'),
    [filesQuery.data],
  )
  const selected = selectedArtifactForPayload(items, payload, state?.selectedArtifactId)
  const groups = useMemo(() => groupByRun(items), [items])
  const view = payload.view ?? (payload.selectedArtifactId ? 'preview' : 'list')

  const handleSelect = (artifact: ArtifactSummary) => {
    selectArtifact({ conversationId: payload.conversationId, artifactId: artifact.id })
    openedMutation.mutate(artifact.id)
    setRightRail({
      mode: 'artifacts',
      artifacts: {
        conversationId: payload.conversationId,
        selectedArtifactId: artifact.id,
        view: 'preview',
      },
    })
  }

  const handleDownloadAll = () => {
    for (const artifact of items) {
      openExternalUrl(resolveImageUrl(artifact.download_url) ?? artifact.download_url)
    }
  }

  if (isLoading && items.length === 0 && attachedFiles.length === 0) {
    return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  }

  if (items.length === 0 && attachedFiles.length === 0) {
    return (
      <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
        {t('emptyPanel')}
      </div>
    )
  }

  if (view === 'list' || !selected) {
    const totalCount = items.length + attachedFiles.length
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-foreground">{t('listTitle')}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('listDescription', { count: totalCount })}
            </p>
          </div>
          {items.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="shrink-0 gap-1.5"
              onClick={handleDownloadAll}
            >
              <DownloadIcon className="size-4" />
              {t('downloadAll')}
            </Button>
          )}
        </div>

        {items.length > 0 && (
          <section className="space-y-2">
            <h4 className="text-xs font-semibold text-muted-foreground">
              {tFiles('generatedSection')}
            </h4>
            <div className="space-y-3">
              {groups.map((group) => (
                <section key={group.runId} className="space-y-2">
                  <div className="text-xs font-medium text-muted-foreground">
                    {t('runLabel', { run: group.runId.slice(0, 8) })}
                  </div>
                  <div className="space-y-1">
                    {group.items.map((artifact) => (
                      <GeneratedFileRow
                        key={artifact.id}
                        artifact={artifact}
                        active={selected?.id === artifact.id}
                        onSelect={handleSelect}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </section>
        )}

        {attachedFiles.length > 0 && (
          <section className="space-y-2">
            <h4 className="text-xs font-semibold text-muted-foreground">
              {tFiles('attachedSection')}
            </h4>
            <div className="space-y-1">
              {attachedFiles.map((file) => (
                <AttachedFileCard key={file.id} file={file} />
              ))}
            </div>
          </section>
        )}
      </div>
    )
  }

  return (
    <section>
      <ArtifactPreview artifact={selected} previewMode={payload.previewMode ?? 'preview'} />
    </section>
  )
}
