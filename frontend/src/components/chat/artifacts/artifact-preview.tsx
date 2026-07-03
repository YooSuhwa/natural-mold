'use client'

import { useQuery } from '@tanstack/react-query'
import { useTranslations } from 'next-intl'
import { artifactKeys, getArtifactTextContent } from '@/lib/api/artifacts'
import type { ArtifactSummary, ArtifactTextContent } from '@/lib/types'
import { ArtifactCodeBlock } from './artifact-code-block'
import { getArtifactPreviewProvider } from './preview-registry'
import { canShowArtifactSource } from './source-capabilities'

interface ArtifactPreviewProps {
  artifact: ArtifactSummary | null
  previewMode?: 'preview' | 'code'
  /**
   * Override the text-content fetch. Attachments are not artifacts, so their
   * text body must be read from `/api/uploads/{id}/content` rather than the
   * artifact content endpoint (which 404s on an upload id).
   */
  textLoader?: (() => Promise<ArtifactTextContent>) | null
}

export function ArtifactPreview({
  artifact,
  previewMode = 'preview',
  textLoader,
}: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  const provider = artifact ? getArtifactPreviewProvider(artifact) : null
  const shouldLoadText = Boolean(
    artifact &&
    (provider?.requiresText || (previewMode === 'code' && canShowArtifactSource(artifact))),
  )
  const textQuery = useQuery({
    queryKey: textLoader
      ? ['upload-content', artifact?.id ?? '']
      : artifactKeys.content(artifact?.id, artifact?.version_id),
    queryFn: () => (textLoader ? textLoader() : getArtifactTextContent(artifact?.id ?? '')),
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
        <ArtifactCodeBlock text={textQuery.data?.text ?? ''} extension={artifact.extension} />
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
