import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { ArtifactPreviewProvider, ArtifactPreviewProps } from '../preview-registry'
import { DocumentPreviewShell } from './document-preview-shell'
import { useArtifactArrayBuffer } from './use-artifact-binary'
import { useTranslations } from 'next-intl'

const MAX_ROWS = 200
const MAX_COLUMNS = 50

interface SheetPreview {
  name: string
  rows: string[][]
  totalRows: number
  totalColumns: number
  truncated: boolean
}

function normalizeRows(rows: unknown[][]): SheetPreview['rows'] {
  return rows.slice(0, MAX_ROWS).map((row) =>
    row.slice(0, MAX_COLUMNS).map((cell) => {
      if (cell === null || cell === undefined) return ''
      if (cell instanceof Date) return cell.toISOString().slice(0, 10)
      return String(cell)
    }),
  )
}

export function XlsxPreview({ artifact }: ArtifactPreviewProps) {
  const t = useTranslations('chat.rightRail.artifacts.documentPreview')
  const binary = useArtifactArrayBuffer(artifact)
  const [sheets, setSheets] = useState<SheetPreview[]>([])
  const [activeSheetName, setActiveSheetName] = useState<string | null>(null)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [isParsing, setIsParsing] = useState(false)

  useEffect(() => {
    if (!binary.data) return
    let cancelled = false
    void (async () => {
      try {
        setIsParsing(true)
        setRenderError(null)
        const XLSX = await import('xlsx')
        const workbook = XLSX.read(binary.data, { type: 'array', cellDates: true })
        const nextSheets = workbook.SheetNames.map((name) => {
          const rawRows = XLSX.utils.sheet_to_json<unknown[]>(workbook.Sheets[name], {
            header: 1,
            raw: false,
            blankrows: false,
          })
          const totalColumns = rawRows.reduce((max, row) => Math.max(max, row.length), 0)
          return {
            name,
            rows: normalizeRows(rawRows),
            totalRows: rawRows.length,
            totalColumns,
            truncated: rawRows.length > MAX_ROWS || totalColumns > MAX_COLUMNS,
          }
        })
        if (!cancelled) {
          setSheets(nextSheets)
          setActiveSheetName(nextSheets[0]?.name ?? null)
        }
      } catch (error) {
        if (!cancelled) setRenderError(error instanceof Error ? error.message : String(error))
      } finally {
        if (!cancelled) setIsParsing(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [binary.data])

  const activeSheet = useMemo(
    () => sheets.find((sheet) => sheet.name === activeSheetName) ?? sheets[0] ?? null,
    [activeSheetName, sheets],
  )
  const error = binary.error instanceof Error ? binary.error.message : renderError
  const columnCount = activeSheet?.rows.reduce((max, row) => Math.max(max, row.length), 0) ?? 0
  const columnIndexes = Array.from({ length: columnCount }, (_, index) => index)

  return (
    <DocumentPreviewShell
      artifact={artifact}
      title={artifact.display_name}
      isLoading={binary.isLoading || isParsing}
      error={error}
      toolbar={
        sheets.length > 1 ? (
          <div className="flex max-w-64 items-center gap-1 overflow-x-auto">
            {sheets.map((sheet) => (
              <Button
                key={sheet.name}
                variant={sheet.name === activeSheet?.name ? 'secondary' : 'ghost'}
                size="xs"
                onClick={() => setActiveSheetName(sheet.name)}
              >
                {sheet.name}
              </Button>
            ))}
          </div>
        ) : null
      }
    >
      {activeSheet && columnCount > 0 ? (
        <div className="max-h-[620px] overflow-auto">
          <table className="w-full border-collapse text-left text-xs">
            <tbody>
              {activeSheet.rows.map((row, rowIndex) => (
                <tr key={`${activeSheet.name}-${rowIndex}`} className="border-b border-border/40">
                  {columnIndexes.map((columnIndex) => (
                    <td
                      key={columnIndex}
                      className="max-w-64 border-r border-border/40 px-3 py-2 align-top text-foreground last:border-r-0"
                    >
                      <span className="break-words">{row[columnIndex] ?? ''}</span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {activeSheet.truncated ? (
            <div className="border-t border-border/60 px-3 py-2 text-xs text-muted-foreground">
              {t('truncatedGrid', { rows: MAX_ROWS, columns: MAX_COLUMNS })}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="px-4 py-8 text-center text-sm text-muted-foreground">{t('loading')}</div>
      )}
    </DocumentPreviewShell>
  )
}

export const XlsxPreviewProvider: ArtifactPreviewProvider = {
  id: 'xlsx',
  priority: 87,
  requiresText: false,
  extensions: ['xlsx', 'xls'],
  mimeTypes: [
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
  ],
  render: (props) => <XlsxPreview {...props} />,
}
