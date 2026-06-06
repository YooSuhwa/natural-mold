import { useTranslations } from 'next-intl'
import { MarkdownContent } from '@/components/chat/markdown-content'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

function MarkdownPreview({ textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  return <MarkdownContent content={textContent?.text ?? ''} />
}

export const MarkdownPreviewProvider: ArtifactPreviewProvider = {
  id: 'markdown',
  priority: 80,
  requiresText: true,
  kinds: ['markdown'],
  extensions: ['md', 'markdown'],
  mimeTypes: ['text/markdown'],
  match: (artifact) => artifact.artifact_kind === 'markdown',
  render: (props) => <MarkdownPreview {...props} />,
}
