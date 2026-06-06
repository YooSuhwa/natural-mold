import { useTranslations } from 'next-intl'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function PdfPreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  return (
    <iframe
      src={resolveImageUrl(artifact.preview_url) ?? ''}
      title={t('pdfPreviewTitle', { name: artifact.display_name })}
      className="h-[520px] w-full border border-border bg-background"
    />
  )
}

export const PdfPreviewProvider: ArtifactPreviewProvider = {
  id: 'pdf',
  priority: 90,
  requiresText: false,
  kinds: ['pdf'],
  extensions: ['pdf'],
  mimeTypes: ['application/pdf'],
  match: (artifact) => artifact.artifact_kind === 'pdf' || artifact.mime_type === 'application/pdf',
  render: (props) => <PdfPreview {...props} />,
}
