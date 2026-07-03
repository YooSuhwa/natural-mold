import { useTranslations } from 'next-intl'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { ArtifactCodeBlock } from '../artifact-code-block'

function CodePreview({ artifact, textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  return (
    <div className="overflow-hidden border border-border bg-card">
      <div className="border-b border-border/60 px-3 py-2 text-xs font-medium text-muted-foreground">
        {artifact.extension ?? t('code')}
      </div>
      <div className="max-h-[520px] overflow-auto p-3">
        <ArtifactCodeBlock text={textContent?.text ?? ''} extension={artifact.extension} />
      </div>
    </div>
  )
}

export const CodePreviewProvider: ArtifactPreviewProvider = {
  id: 'code',
  priority: 75,
  requiresText: true,
  kinds: ['code'],
  match: (artifact) => artifact.artifact_kind === 'code',
  render: (props) => <CodePreview {...props} />,
}
