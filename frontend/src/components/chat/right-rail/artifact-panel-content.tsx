'use client'

import { useMemo } from 'react'
import { useAtomValue, useSetAtom } from 'jotai'
import { DownloadIcon, FileIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { useConversationArtifacts } from '@/lib/hooks/use-conversation-artifacts'
import { useRecordArtifactOpened } from '@/lib/hooks/use-artifact-library'
import { chatArtifactsAtom, selectChatArtifactAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom, type ArtifactsPayload } from '@/lib/stores/chat-right-rail'
import { openExternalUrl } from '@/lib/browser/window-open'
import type { ArtifactSummary } from '@/lib/types'
import { cn, resolveImageUrl } from '@/lib/utils'

interface Props {
  payload: ArtifactsPayload
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
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

export function ArtifactPanelContent({ payload }: Props) {
  const t = useTranslations('chat.rightRail.artifacts')
  const { isLoading } = useConversationArtifacts(payload.conversationId)
  const state = useAtomValue(chatArtifactsAtom)[payload.conversationId]
  const selectArtifact = useSetAtom(selectChatArtifactAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const openedMutation = useRecordArtifactOpened()
  const items = useMemo(() => state?.items ?? [], [state?.items])
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

  if (isLoading && items.length === 0) {
    return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  }

  if (items.length === 0) {
    return (
      <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
        {t('emptyPanel')}
      </div>
    )
  }

  if (view === 'list' || !selected) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-foreground">{t('listTitle')}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('listDescription', { count: items.length })}
            </p>
          </div>
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
        </div>
        <div className="space-y-3">
          {groups.map((group) => (
            <section key={group.runId} className="space-y-2">
              <div className="text-xs font-medium text-muted-foreground">
                {t('runLabel', { run: group.runId.slice(0, 8) })}
              </div>
              <div className="space-y-1">
                {group.items.map((artifact) => {
                  const active = selected?.id === artifact.id
                  return (
                    <button
                      key={artifact.id}
                      type="button"
                      className={cn(
                        'moldy-chat-card moldy-card-hover flex w-full items-center gap-3 px-3 py-3 text-left text-sm',
                        active && 'border-primary bg-accent',
                      )}
                      onClick={() => handleSelect(artifact)}
                    >
                      <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-muted-foreground">
                        <FileIcon className="size-5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-foreground">
                          {artifact.display_name}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {t(`kinds.${artifact.artifact_kind}`)} ·{' '}
                          {artifact.extension?.toUpperCase() ?? formatBytes(artifact.size_bytes)}
                        </span>
                      </span>
                      <Badge variant="outline">{formatBytes(artifact.size_bytes)}</Badge>
                    </button>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
      </div>
    )
  }

  return (
    <section>
      <ArtifactPreview artifact={selected} previewMode={payload.previewMode ?? 'preview'} />
    </section>
  )
}
