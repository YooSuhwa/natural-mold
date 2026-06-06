import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { parseTablePreview } from '../data-preview-utils'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'

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

function TableDataPreview({ artifact, textContent, isLoadingText }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts')
  const tData = useTranslations('chat.rightRail.artifacts.data')
  const text = textContent?.text ?? ''
  const parsed = useMemo(
    () => parseTablePreview(text, artifact.extension),
    [artifact.extension, text],
  )

  if (isLoadingText) return <div className="text-sm text-muted-foreground">{t('loading')}</div>
  if (parsed.error || !parsed.data) return <ParseFallback text={text} />
  if (!parsed.data.totalColumns) {
    return (
      <div className="moldy-muted-panel px-4 py-8 text-center text-sm text-muted-foreground">
        {tData('emptyTable')}
      </div>
    )
  }

  const columnIndexes = Array.from({ length: parsed.data.totalColumns }, (_, index) => index)

  return (
    <div className="overflow-hidden border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border/60 px-3 py-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          {artifact.extension?.toUpperCase() ?? 'CSV'}
        </span>
        <span>
          {tData('tableSummary', {
            rows: parsed.data.totalRows,
            columns: parsed.data.totalColumns,
          })}
        </span>
      </div>
      <div className="max-h-[520px] overflow-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {columnIndexes.map((index) => (
                <th
                  key={index}
                  scope="col"
                  className="border-b border-r border-border/60 px-3 py-2 font-medium text-foreground last:border-r-0"
                >
                  {parsed.data?.headers[index] || tData('columnFallback', { number: index + 1 })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {parsed.data.rows.map((row, rowIndex) => (
              <tr key={`${rowIndex}-${row.join('|')}`} className="border-b border-border/40">
                {columnIndexes.map((index) => (
                  <td
                    key={index}
                    className="max-w-64 border-r border-border/40 px-3 py-2 align-top text-foreground last:border-r-0"
                  >
                    <span className="break-words">{row[index] ?? ''}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {parsed.data.truncated ? (
        <div className="border-t border-border/60 px-3 py-2 text-xs text-muted-foreground">
          {tData('tableTruncated', { rows: parsed.data.shownRows })}
        </div>
      ) : null}
    </div>
  )
}

export const TableDataPreviewProvider: ArtifactPreviewProvider = {
  id: 'table-data',
  priority: 78,
  requiresText: true,
  extensions: ['csv', 'tsv'],
  mimeTypes: ['text/csv', 'text/tab-separated-values'],
  render: (props) => <TableDataPreview {...props} />,
}
