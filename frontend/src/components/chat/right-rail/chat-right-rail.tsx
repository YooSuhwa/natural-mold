'use client'

import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAtom, useAtomValue, useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { useViewportWidth } from '@/hooks/use-viewport-width'
import {
  CheckIcon,
  CodeIcon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  MoreHorizontalIcon,
  RefreshCcwIcon,
  StarIcon,
  XIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { HorizontalResizeHandle } from '@/components/shared/horizontal-resize-handle'
import { canShowArtifactSource } from '@/components/chat/artifacts/source-capabilities'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { getArtifactTextContent, artifactKeys } from '@/lib/api/artifacts'
import { useSetArtifactFavorite } from '@/lib/hooks/use-artifact-library'
import { cn, resolveImageUrl } from '@/lib/utils'
import { chatArtifactsAtom, type ChatArtifactsState } from '@/lib/stores/chat-artifacts'
import {
  chatRightRailAtom,
  chatRightRailWidthAtom,
  clampRightRailWidth,
  RIGHT_RAIL_COLLAPSE_THRESHOLD_PX,
  RIGHT_RAIL_WIDTH_DEFAULT_PX,
  RIGHT_RAIL_WIDTH_MAX_PX,
  RIGHT_RAIL_WIDTH_MIN_PX,
  type ArtifactsPayload,
  type RightRailState,
} from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import { SubagentPanelContent } from './subagent-panel-content'
import { ToolResultPanelContent } from './tool-result-panel-content'
import { OutlinePanelContent } from './outline-panel-content'
import { ArtifactPanelContent } from './artifact-panel-content'

interface Props {
  className?: string
  conversationId?: string | null
}

function conversationIdForState(state: RightRailState): string | null | undefined {
  if (state.mode === 'subagent') return state.subagent.conversationId
  if (state.mode === 'tool-result') return state.toolResult.conversationId
  if (state.mode === 'outline') return state.outline.conversationId
  if (state.mode === 'artifacts') return state.artifacts.conversationId
  return undefined
}

export function ChatRightRail({ className, conversationId }: Props) {
  const t = useTranslations('chat.rightRail')
  const [state, setState] = useAtom(chatRightRailAtom)
  const [storedWidth, setStoredWidth] = useAtom(chatRightRailWidthAtom)
  const [previewWidth, setPreviewWidth] = useState<number | null>(null)
  const stateConversationId = conversationIdForState(state)
  const viewportWidth = useViewportWidth()
  const isStaleConversation =
    state.mode !== 'none' &&
    conversationId !== undefined &&
    conversationId !== null &&
    stateConversationId !== undefined &&
    stateConversationId !== null &&
    stateConversationId !== conversationId
  const isOpen = state.mode !== 'none' && !isStaleConversation
  const maxWidth = clampRightRailWidth(RIGHT_RAIL_WIDTH_MAX_PX, viewportWidth)
  const stableWidth = clampRightRailWidth(storedWidth, viewportWidth)
  const effectiveWidth = isOpen ? Math.min(previewWidth ?? stableWidth, maxWidth) : 0
  const isCollapsePreview = previewWidth !== null && previewWidth < RIGHT_RAIL_COLLAPSE_THRESHOLD_PX
  const rightRailStyle = useMemo<CSSProperties & { '--chat-right-rail-width': string }>(
    () => ({
      '--chat-right-rail-width': `${effectiveWidth}px`,
      width: isOpen ? 'var(--chat-right-rail-width)' : 0,
    }),
    [effectiveWidth, isOpen],
  )

  const previewRightRailWidth = useCallback(
    (width: number) => {
      const nextWidth = Number.isFinite(width) ? Math.min(Math.max(width, 0), maxWidth) : 0
      setPreviewWidth(nextWidth)
    },
    [maxWidth],
  )

  const commitRightRailWidth = useCallback(
    (width: number) => {
      const nextWidth = clampRightRailWidth(width, viewportWidth)
      setPreviewWidth(null)
      setStoredWidth(nextWidth)
    },
    [setStoredWidth, viewportWidth],
  )

  const closeRightRail = useCallback(() => {
    setPreviewWidth(null)
    setState({ mode: 'none' })
  }, [setState])
  const cancelRightRailPreview = useCallback(() => {
    setPreviewWidth(null)
  }, [])

  useEffect(() => {
    if (isStaleConversation) {
      setState({ mode: 'none' })
    }
  }, [isStaleConversation, setState])

  return (
    <>
      {/* 데스크톱: inline split */}
      <aside
        data-slot="chat-right-rail"
        data-collapse-preview={isCollapsePreview ? 'true' : undefined}
        className={cn(
          'relative hidden shrink-0 overflow-hidden bg-muted/30 transition-[width] duration-200 md:block',
          className,
        )}
        style={rightRailStyle}
        aria-hidden={!isOpen}
      >
        {isOpen ? (
          <>
            <HorizontalResizeHandle
              ariaLabel={t('resizePanel')}
              className="absolute inset-y-0 left-0 z-20 hidden md:flex"
              collapsedValueText={t('collapsedPanel')}
              collapseThreshold={RIGHT_RAIL_COLLAPSE_THRESHOLD_PX}
              maxWidth={maxWidth}
              minWidth={RIGHT_RAIL_WIDTH_MIN_PX}
              onCancelPreview={cancelRightRailPreview}
              onCollapse={closeRightRail}
              onCommitWidth={commitRightRailWidth}
              onPreviewWidth={previewRightRailWidth}
              onReset={() => commitRightRailWidth(RIGHT_RAIL_WIDTH_DEFAULT_PX)}
              side="right"
              variantClassName="moldy-right-rail-resize-handle"
              width={effectiveWidth}
            />
            <RailFrame state={state} className="w-full" onClose={closeRightRail} />
          </>
        ) : null}
      </aside>

      {/* 모바일: artifact는 독립 full-screen layer, 그 외 rail은 기존 drawer */}
      {isOpen ? (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true">
          {state.mode === 'artifacts' ? (
            <div className="moldy-artifact-mobile-layer absolute inset-0">
              <RailFrame state={state} className="h-full w-full" onClose={closeRightRail} />
            </div>
          ) : (
            <>
              <button
                type="button"
                aria-label={t('closePanel')}
                className="absolute inset-0 bg-background/60 backdrop-blur-sm"
                onClick={closeRightRail}
              />
              <div className="moldy-side-panel moldy-right-rail-mobile absolute inset-y-0 right-0">
                <RailFrame state={state} className="h-full w-full" onClose={closeRightRail} />
              </div>
            </>
          )}
        </div>
      ) : null}
    </>
  )
}

interface RailFrameProps {
  state: RightRailState
  className?: string
  onClose: () => void
}

function RailFrame({ state, className, onClose }: RailFrameProps) {
  const t = useTranslations('chat.rightRail')
  const artifactsByConversation = useAtomValue(chatArtifactsAtom)
  const selectedArtifact =
    state.mode === 'artifacts' ? artifactForPayload(state.artifacts, artifactsByConversation) : null
  const artifactView =
    state.mode === 'artifacts'
      ? (state.artifacts.view ?? (state.artifacts.selectedArtifactId ? 'preview' : 'list'))
      : null
  const title =
    state.mode === 'artifacts'
      ? selectedArtifact && artifactView === 'preview'
        ? selectedArtifact.display_name
        : t('artifacts.title')
      : titleFor(state)

  return (
    <div data-slot="chat-right-rail-frame" className={cn('flex h-full flex-col', className)}>
      {state.mode === 'artifacts' && selectedArtifact && artifactView === 'preview' ? (
        <ArtifactViewerHeader
          artifact={selectedArtifact}
          payload={state.artifacts}
          title={title}
          onClose={onClose}
        />
      ) : state.mode === 'artifacts' ? (
        <header className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2.5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            onClick={onClose}
            aria-label={t('closePanel')}
          >
            <XIcon className="size-4" />
          </Button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{title}</h2>
          <Button
            variant="ghost"
            size="icon-sm"
            className="hidden md:inline-flex"
            onClick={onClose}
            aria-label={t('closePanel')}
          >
            <XIcon className="size-4" />
          </Button>
        </header>
      ) : (
        <header className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-3">
          <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t('closePanel')}>
            <XIcon className="size-4" />
          </Button>
        </header>
      )}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {state.mode === 'subagent' ? <SubagentPanelContent payload={state.subagent} /> : null}
        {state.mode === 'tool-result' ? (
          <ToolResultPanelContent payload={state.toolResult} />
        ) : null}
        {state.mode === 'outline' ? <OutlinePanelContent payload={state.outline} /> : null}
        {state.mode === 'artifacts' ? <ArtifactPanelContent payload={state.artifacts} /> : null}
      </div>
    </div>
  )
}

function artifactForPayload(
  payload: ArtifactsPayload,
  artifactsByConversation: ChatArtifactsState,
): ArtifactSummary | null {
  const artifactState = artifactsByConversation[payload.conversationId]
  const items = artifactState?.items ?? []
  const selectedIds = [payload.selectedArtifactId, artifactState?.selectedArtifactId]
  for (const selectedId of selectedIds) {
    const artifact = items.find((item) => item.id === selectedId)
    if (artifact) return artifact
  }
  return items[0] ?? null
}

function downloadArtifact(artifact: ArtifactSummary): void {
  const href = resolveImageUrl(artifact.download_url) ?? artifact.download_url
  const link = document.createElement('a')
  link.href = href
  link.download = artifact.display_name
  link.rel = 'noopener'
  document.body.append(link)
  link.click()
  link.remove()
}

interface ArtifactViewerHeaderProps {
  artifact: ArtifactSummary
  payload: ArtifactsPayload
  title: string
  onClose: () => void
}

function ArtifactViewerHeader({ artifact, payload, title, onClose }: ArtifactViewerHeaderProps) {
  const t = useTranslations('chat.rightRail')
  const tArtifacts = useTranslations('chat.rightRail.artifacts')
  const setRightRail = useSetAtom(chatRightRailAtom)
  const favoriteMutation = useSetArtifactFavorite()
  const queryClient = useQueryClient()
  const [copied, setCopied] = useState(false)
  const previewMode = payload.previewMode ?? 'preview'
  const canShowSource = canShowArtifactSource(artifact)

  const setPreviewMode = useCallback(
    (mode: 'preview' | 'code') => {
      setRightRail({
        mode: 'artifacts',
        artifacts: {
          ...payload,
          selectedArtifactId: artifact.id,
          view: 'preview',
          previewMode: mode,
        },
      })
    },
    [artifact.id, payload, setRightRail],
  )

  const handleCopy = useCallback(async () => {
    if (!canShowSource) return
    try {
      const content = await getArtifactTextContent(artifact.id)
      await navigator.clipboard.writeText(content.text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch (error) {
      console.warn('[ArtifactViewerHeader] failed to copy artifact text', error)
    }
  }, [artifact.id, canShowSource])

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: artifactKeys.content(artifact.id, artifact.version_id),
    })
    queryClient.invalidateQueries({ queryKey: artifactKeys.conversation(artifact.conversation_id) })
  }, [artifact.conversation_id, artifact.id, artifact.version_id, queryClient])

  const handleFavorite = useCallback(() => {
    favoriteMutation.mutate({
      artifactId: artifact.id,
      isFavorite: !artifact.is_favorite,
    })
  }, [artifact.id, artifact.is_favorite, favoriteMutation])

  return (
    <header className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2.5">
      <Button
        variant="ghost"
        size="icon-sm"
        className="md:hidden"
        onClick={onClose}
        aria-label={t('closePanel')}
      >
        <XIcon className="size-4" />
      </Button>
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <div className="hidden shrink-0 items-center gap-1 md:flex">
          <Button
            variant={previewMode === 'preview' ? 'secondary' : 'ghost'}
            size="icon-sm"
            aria-label={tArtifacts('previewMode')}
            onClick={() => setPreviewMode('preview')}
          >
            <EyeIcon className="size-4" />
          </Button>
          <Button
            variant={previewMode === 'code' ? 'secondary' : 'ghost'}
            size="icon-sm"
            aria-label={tArtifacts('codeMode')}
            disabled={!canShowSource}
            onClick={() => setPreviewMode('code')}
          >
            <CodeIcon className="size-4" />
          </Button>
        </div>
        <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
        <span className="hidden shrink-0 text-xs text-muted-foreground md:inline">
          · {artifact.extension?.toUpperCase() ?? tArtifacts(`kinds.${artifact.artifact_kind}`)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          className="hidden md:inline-flex"
          aria-label={tArtifacts('download')}
          render={
            <a href={resolveImageUrl(artifact.download_url) ?? artifact.download_url} download />
          }
        >
          <DownloadIcon className="size-4" />
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={tArtifacts('openMenu')} />}
          >
            <MoreHorizontalIcon className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="moldy-popover w-52">
            <DropdownMenuItem onClick={() => setPreviewMode('preview')}>
              {previewMode === 'preview' ? <CheckIcon /> : <EyeIcon />}
              {tArtifacts('previewMode')}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={!canShowSource} onClick={() => setPreviewMode('code')}>
              {previewMode === 'code' ? <CheckIcon /> : <CodeIcon />}
              {tArtifacts('codeMode')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled={!canShowSource} onClick={() => void handleCopy()}>
              <CopyIcon />
              {copied ? tArtifacts('copied') : tArtifacts('copyContents')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => downloadArtifact(artifact)}>
              <DownloadIcon />
              {tArtifacts('downloadOriginal')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={handleFavorite}>
              <StarIcon className={cn(artifact.is_favorite && 'fill-current text-amber-500')} />
              {tArtifacts(artifact.is_favorite ? 'unfavorite' : 'favorite')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button
          variant="ghost"
          size="icon-sm"
          className="hidden md:inline-flex"
          aria-label={tArtifacts('refreshPreview')}
          onClick={handleRefresh}
        >
          <RefreshCcwIcon className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          className="hidden md:inline-flex"
          onClick={onClose}
          aria-label={t('closePanel')}
        >
          <XIcon className="size-4" />
        </Button>
      </div>
    </header>
  )
}

function titleFor(state: RightRailState): string {
  if (state.mode === 'subagent') return state.subagent.agentName || 'Sub-agent'
  if (state.mode === 'tool-result') return state.toolResult.toolName
  if (state.mode === 'outline') return 'Outline'
  return ''
}
