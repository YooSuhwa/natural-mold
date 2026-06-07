import { useTranslations } from 'next-intl'
import { MarkdownContent } from '@/components/chat/markdown-content'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

export function MermaidPreview({ textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  const source = textContent?.text?.trim() ?? ''
  return <MarkdownContent content={`\`\`\`mermaid\n${source}\n\`\`\``} />
}

export const MermaidPreviewProvider: ArtifactPreviewProvider = {
  id: 'mermaid',
  priority: 82,
  requiresText: true,
  extensions: ['mmd', 'mermaid'],
  match: (artifact) => ['mmd', 'mermaid'].includes(artifact.extension ?? ''),
  render: (props) => <MermaidPreview {...props} />,
}
