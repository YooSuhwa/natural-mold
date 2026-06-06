import { useTranslations } from 'next-intl'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function HtmlPreview({ artifact, textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  return (
    <iframe
      sandbox=""
      srcDoc={textContent?.text ?? ''}
      title={t('htmlPreviewTitle', { name: artifact.display_name })}
      className="h-[520px] w-full border border-border bg-background"
    />
  )
}

export const HtmlPreviewProvider: ArtifactPreviewProvider = {
  id: 'html',
  priority: 85,
  requiresText: true,
  kinds: ['html'],
  extensions: ['html', 'htm'],
  mimeTypes: ['text/html'],
  match: (artifact) => artifact.artifact_kind === 'html' || artifact.mime_type === 'text/html',
  render: (props) => <HtmlPreview {...props} />,
}
