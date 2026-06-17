'use client'

import { useMemo, useState } from 'react'
import { DownloadIcon, FileIcon, StarIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { ArtifactLibraryFilters } from './artifact-library-filters'
import { ArtifactLibraryStatsView } from './artifact-library-stats'
import { useAgentSummaries } from '@/lib/hooks/use-agents'
import {
  useArtifactLibrary,
  useArtifactLibraryStats,
  useRecentArtifacts,
  useRecordArtifactOpened,
  useSetArtifactFavorite,
} from '@/lib/hooks/use-artifact-library'
import type { ArtifactKind, ArtifactSummary } from '@/lib/types'
import { cn, resolveImageUrl } from '@/lib/utils'
import { formatDisplayBytes, formatDisplayDateTime } from '@/lib/utils/display-format'

const ALL = 'all'

export function ArtifactLibraryContent() {
  const t = useTranslations('artifacts')
  const tKinds = useTranslations('chat.rightRail.artifacts.kinds')
  const [q, setQ] = useState('')
  const [agentId, setAgentId] = useState(ALL)
  const [conversationId, setConversationId] = useState('')
  const [kind, setKind] = useState(ALL)
  const [favorite, setFavorite] = useState(ALL)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data: agents } = useAgentSummaries()
  const params = useMemo(
    () => ({
      q: q.trim() || null,
      agent_id: agentId === ALL ? null : agentId,
      conversation_id: conversationId.trim() || null,
      kind: kind === ALL ? null : (kind as ArtifactKind),
      favorite: favorite === 'favorite' ? true : null,
      limit: 50,
    }),
    [agentId, conversationId, favorite, kind, q],
  )
  const library = useArtifactLibrary(params)
  const stats = useArtifactLibraryStats()
  const recent = useRecentArtifacts(8)
  const favoriteMutation = useSetArtifactFavorite()
  const openedMutation = useRecordArtifactOpened()
  const items = library.data?.pages.flatMap((page) => page.items) ?? []
  const hasActiveFilters =
    q.trim() !== '' ||
    agentId !== ALL ||
    conversationId.trim() !== '' ||
    kind !== ALL ||
    favorite !== ALL
  const selectedFromItems = items.find((item) => item.id === selectedId)
  const selectedFromRecent =
    !hasActiveFilters && selectedId ? recent.data?.find((item) => item.id === selectedId) : null
  const selected = selectedFromItems ?? selectedFromRecent ?? items[0] ?? null
  function selectArtifact(artifact: ArtifactSummary) {
    setSelectedId(artifact.id)
    openedMutation.mutate(artifact.id)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t('title')}</h1>
        <p className="text-sm text-muted-foreground">{t('description')}</p>
      </header>

      <ArtifactLibraryStatsView stats={stats.data} />

      <section className="moldy-panel space-y-4 p-4">
        <ArtifactLibraryFilters
          q={q}
          onQChange={setQ}
          agentId={agentId}
          onAgentIdChange={setAgentId}
          conversationId={conversationId}
          onConversationIdChange={setConversationId}
          kind={kind}
          onKindChange={setKind}
          favorite={favorite}
          onFavoriteChange={setFavorite}
          agents={agents}
        />
      </section>

      <div className="grid min-h-0 flex-1 gap-5 xl:grid-cols-[minmax(360px,0.95fr)_minmax(420px,1.05fr)]">
        <section className="moldy-panel min-h-[420px] overflow-hidden">
          <div className="moldy-panel-header flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">{t('listTitle')}</h2>
            <span className="text-xs text-muted-foreground">
              {t('count', { count: items.length })}
            </span>
          </div>
          <div className="max-h-[640px] overflow-y-auto p-3">
            {library.isLoading ? (
              <div className="p-4 text-sm text-muted-foreground">{t('loading')}</div>
            ) : items.length === 0 ? (
              <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
                {t('empty')}
              </div>
            ) : (
              <div className="space-y-2">
                {items.map((artifact) => {
                  const active = selected?.id === artifact.id
                  return (
                    <button
                      key={artifact.id}
                      type="button"
                      className={cn(
                        'moldy-card flex w-full items-center gap-3 p-3 text-left hover:bg-accent',
                        active && 'border-primary bg-accent',
                      )}
                      onClick={() => selectArtifact(artifact)}
                    >
                      <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-foreground">
                          {artifact.display_name}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {artifact.agent_name ?? artifact.conversation_title ?? artifact.path}
                        </span>
                      </span>
                      <span className="flex shrink-0 flex-col items-end gap-1">
                        <Badge variant="outline">{tKinds(artifact.artifact_kind)}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {formatDisplayBytes(artifact.size_bytes)}
                        </span>
                      </span>
                    </button>
                  )
                })}
                {library.hasNextPage ? (
                  <div className="pt-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="w-full"
                      disabled={library.isFetchingNextPage}
                      onClick={() => void library.fetchNextPage()}
                    >
                      {library.isFetchingNextPage ? t('loadingMore') : t('loadMore')}
                    </Button>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </section>

        <section className="moldy-panel min-h-[420px] overflow-hidden">
          <div className="moldy-panel-header flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-foreground">
                {selected?.display_name ?? t('previewTitle')}
              </h2>
              {selected ? (
                <p className="truncate text-xs text-muted-foreground">
                  {formatDisplayDateTime(selected.updated_at)}
                </p>
              ) : null}
            </div>
            {selected ? (
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t(selected.is_favorite ? 'unfavorite' : 'favorite')}
                  onClick={() =>
                    favoriteMutation.mutate({
                      artifactId: selected.id,
                      isFavorite: !selected.is_favorite,
                    })
                  }
                >
                  <StarIcon
                    className={cn('size-4', selected.is_favorite && 'fill-current text-amber-500')}
                  />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t('download')}
                  render={
                    <a
                      href={resolveImageUrl(selected.download_url) ?? selected.download_url}
                      download
                    />
                  }
                >
                  <DownloadIcon className="size-4" />
                </Button>
              </div>
            ) : null}
          </div>
          <div className="max-h-[680px] overflow-y-auto p-4">
            <ArtifactPreview artifact={selected} />
          </div>
        </section>
      </div>

      {recent.data && recent.data.length > 0 ? (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-foreground">{t('recentTitle')}</h2>
          <div className="grid gap-2 md:grid-cols-4">
            {recent.data.map((artifact) => (
              <button
                key={artifact.id}
                type="button"
                className="moldy-card flex items-center gap-2 p-3 text-left hover:bg-accent"
                onClick={() => selectArtifact(artifact)}
              >
                <FileIcon className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">
                    {artifact.display_name}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {tKinds(artifact.artifact_kind)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  )
}
