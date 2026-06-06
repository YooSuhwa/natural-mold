import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function MediaPreview({ artifact }: ArtifactPreviewProps) {
  const src = resolveImageUrl(artifact.preview_url) ?? ''
  if (artifact.artifact_kind === 'audio' || artifact.mime_type.startsWith('audio/')) {
    return <audio src={src} controls className="w-full" />
  }
  return <video src={src} controls className="max-h-[520px] w-full bg-background" />
}

export const MediaPreviewProvider: ArtifactPreviewProvider = {
  id: 'media',
  priority: 95,
  requiresText: false,
  kinds: ['audio', 'video'],
  mimeTypes: ['audio/*', 'video/*'],
  match: (artifact) =>
    artifact.artifact_kind === 'audio' ||
    artifact.artifact_kind === 'video' ||
    artifact.mime_type.startsWith('audio/') ||
    artifact.mime_type.startsWith('video/'),
  render: (props) => <MediaPreview {...props} />,
}
