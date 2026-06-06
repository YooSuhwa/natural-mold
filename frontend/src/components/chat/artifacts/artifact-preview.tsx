'use client'

import { useQuery } from '@tanstack/react-query'
import { useTranslations } from 'next-intl'
import { getArtifactTextContent } from '@/lib/api/artifacts'
import type { ArtifactSummary } from '@/lib/types'
import { getArtifactPreviewProvider } from './preview-registry'
import { canShowArtifactSource } from './source-capabilities'

interface ArtifactPreviewProps {
  artifact: ArtifactSummary | null
  previewMode?: 'preview' | 'code'
}

export function ArtifactPreview({ artifact, previewMode = 'preview' }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  const provider = artifact ? getArtifactPreviewProvider(artifact) : null
  const shouldLoadText = Boolean(
    artifact &&
    (provider?.requiresText || (previewMode === 'code' && canShowArtifactSource(artifact))),
  )
  const textQuery = useQuery({
    queryKey: ['artifacts', 'content', artifact?.id ?? 'none', artifact?.version_id ?? 'none'],
    queryFn: () => getArtifactTextContent(artifact?.id ?? ''),
    enabled: shouldLoadText,
    staleTime: 30_000,
  })

  if (!artifact || !provider) {
    return (
      <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
        {t('emptySelection')}
      </div>
    )
  }

  if (previewMode === 'code') {
    if (!canShowArtifactSource(artifact)) {
      return (
        <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
          {t('sourceUnavailable')}
        </div>
      )
    }
    if (textQuery.isLoading) {
      return <div className="text-sm text-muted-foreground">{t('loading')}</div>
    }
    return (
      <div className="moldy-muted-panel max-h-96 overflow-auto p-3">
        <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
          {textQuery.data?.text ?? ''}
        </pre>
        {textQuery.data?.truncated ? (
          <p className="mt-3 text-xs text-muted-foreground">{t('truncated')}</p>
        ) : null}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {provider.render({
        artifact,
        textContent: textQuery.data ?? null,
        isLoadingText: textQuery.isLoading,
      })}
      {textQuery.data?.truncated ? (
        <p className="text-xs text-muted-foreground">{t('truncated')}</p>
      ) : null}
    </div>
  )
}
