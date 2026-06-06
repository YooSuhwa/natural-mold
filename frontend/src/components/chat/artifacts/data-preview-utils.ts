export const MAX_TABLE_PREVIEW_ROWS = 100

export type StructuredPreviewValue =
  | null
  | boolean
  | number
  | string
  | StructuredPreviewValue[]
  | { [key: string]: StructuredPreviewValue }

export interface TablePreviewData {
  headers: string[]
  rows: string[][]
  totalRows: number
  shownRows: number
  totalColumns: number
  truncated: boolean
}

export interface ParseResult<T> {
  data: T | null
  error: string | null
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function cellToString(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  return JSON.stringify(value)
}

export async function parseTablePreview(
  text: string,
  extension: string | null | undefined,
): Promise<ParseResult<TablePreviewData>> {
  const delimiter = extension?.toLowerCase() === 'tsv' ? '\t' : ','
  try {
    const { parse: parseCsv } = await import('csv-parse/browser/esm/sync')
    const records = parseCsv(text, {
      bom: true,
      delimiter,
      relax_column_count: true,
      skip_empty_lines: true,
    }) as unknown[][]
    const rows = records.map((row) => row.map(cellToString))
    const totalColumns = rows.reduce((max, row) => Math.max(max, row.length), 0)
    const headers = rows[0] ?? []
    const dataRows = rows.slice(1)
    const shownRows = dataRows.slice(0, MAX_TABLE_PREVIEW_ROWS)

    return {
      data: {
        headers,
        rows: shownRows,
        totalRows: dataRows.length,
        shownRows: shownRows.length,
        totalColumns,
        truncated: dataRows.length > shownRows.length,
      },
      error: null,
    }
  } catch (error) {
    return { data: null, error: errorMessage(error) }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function normalizeStructuredValue(value: unknown): StructuredPreviewValue {
  if (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return value
  }
  if (typeof value === 'bigint') return value.toString()
  if (value instanceof Date) return value.toISOString()
  if (Array.isArray(value)) return value.map(normalizeStructuredValue)
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, nested]) => [key, normalizeStructuredValue(nested)]),
    )
  }
  return String(value)
}

export function parseJsonPreview(text: string): ParseResult<StructuredPreviewValue> {
  try {
    return { data: normalizeStructuredValue(JSON.parse(text) as unknown), error: null }
  } catch (error) {
    return { data: null, error: errorMessage(error) }
  }
}

export async function parseStructuredDataPreview(
  text: string,
  extension: string | null | undefined,
): Promise<ParseResult<StructuredPreviewValue>> {
  try {
    const parsed =
      extension?.toLowerCase() === 'toml'
        ? (await import('smol-toml')).parse(text)
        : ((await import('yaml')).parse(text) as unknown)
    return { data: normalizeStructuredValue(parsed), error: null }
  } catch (error) {
    return { data: null, error: errorMessage(error) }
  }
}
