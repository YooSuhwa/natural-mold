import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { parseStructuredDataPreview } from '../data-preview-utils'
import type { ParseResult, StructuredPreviewValue } from '../data-preview-utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { StructuredValueTree } from './structured-value-tree'

function ParseFallback({ text }: { text: string }) {
  const t = useTranslations('chat.rightRail.artifacts.data')
  return (
    <div className="space-y-2">
      <div className="moldy-muted-panel px-3 py-2 text-xs text-muted-foreground">
        {t('parseError')}
      </div>
      <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap border border-border bg-card p-3 text-xs leading-relaxed">
        {text}
      </pre>
    </div>
  )
}

export function StructuredDataPreview({
  artifact,
  textContent,
  isLoadingText,
}: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  const text = textContent?.text ?? ''
  const [parsed, setParsed] = useState<ParseResult<StructuredPreviewValue>>({
    data: null,
    error: null,
  })
  const [isParsing, setIsParsing] = useState(false)

  useEffect(() => {
    if (isLoadingText) return
    let cancelled = false
    void (async () => {
      await Promise.resolve()
      if (cancelled) return
      setIsParsing(true)
      try {
        const result = await parseStructuredDataPreview(text, artifact.extension)
        if (!cancelled) setParsed(result)
      } catch (error: unknown) {
        if (!cancelled) {
          setParsed({ data: null, error: error instanceof Error ? error.message : String(error) })
        }
      } finally {
        if (!cancelled) setIsParsing(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [artifact.extension, isLoadingText, text])

  if (isLoadingText || isParsing) {
    return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  }
  if (parsed.error) return <ParseFallback text={text} />
  if (parsed.data === null) return <StructuredValueTree value={null} />
  return <StructuredValueTree value={parsed.data} />
}

export const StructuredDataPreviewProvider: ArtifactPreviewProvider = {
  id: 'structured-data',
  priority: 78,
  requiresText: true,
  extensions: ['yaml', 'yml', 'toml'],
  mimeTypes: [
    'application/yaml',
    'application/x-yaml',
    'text/yaml',
    'application/toml',
    'text/toml',
  ],
  render: (props) => <StructuredDataPreview {...props} />,
}
