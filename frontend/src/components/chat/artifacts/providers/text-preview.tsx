import { useTranslations } from 'next-intl'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function TextPreview({ textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  return (
    <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap border border-border bg-card p-3 text-xs leading-relaxed">
      {textContent?.text ?? t('empty')}
    </pre>
  )
}

export const TextPreviewProvider: ArtifactPreviewProvider = {
  id: 'text',
  priority: 60,
  requiresText: true,
  extensions: ['txt', 'log'],
  mimeTypes: ['text/*'],
  match: (artifact) =>
    artifact.mime_type.startsWith('text/') ||
    ['txt', 'log'].includes(artifact.extension ?? ''),
  render: (props) => <TextPreview {...props} />,
}
