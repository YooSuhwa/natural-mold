/* eslint-disable @next/next/no-img-element */
import { useTranslations } from 'next-intl'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function ImagePreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  return (
    <div className="moldy-muted-panel overflow-hidden">
      <img
        src={resolveImageUrl(artifact.preview_url) ?? resolveImageUrl(artifact.download_url) ?? ''}
        alt={artifact.display_name}
        className="max-h-[520px] w-full object-contain"
      />
      <div className="border-t border-border/60 px-3 py-2 text-xs text-muted-foreground">
        {t('imagePreview')}
      </div>
    </div>
  )
}

export const ImagePreviewProvider: ArtifactPreviewProvider = {
  id: 'image',
  priority: 100,
  requiresText: false,
  kinds: ['image'],
  mimeTypes: ['image/*'],
  match: (artifact) => artifact.artifact_kind === 'image' || artifact.mime_type.startsWith('image/'),
  render: (props) => <ImagePreview {...props} />,
}
