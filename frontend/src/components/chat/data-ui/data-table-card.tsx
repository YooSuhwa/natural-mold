'use client'

import { useMemo } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { DataTable } from '@/components/ui/data-table'

export interface DataTableColumnSpec {
  key: string
  header: string
}

export interface DataTableCardProps {
  columns: DataTableColumnSpec[]
  rows: Record<string, unknown>[]
  title?: string
  searchable?: boolean
}

/** Render any cell value as text only (R2 security: never raw HTML). */
function formatCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * Phase 2 generative-UI component: renders a typed ``data_table`` payload via the
 * reusable tanstack DataTable. The adapter maps the payload's ``{key, header}``
 * columns to ColumnDef and renders every cell as text.
 */
export function DataTableCard({ columns, rows, title, searchable }: DataTableCardProps) {
  const columnDefs = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      columns.map((column) => ({
        accessorKey: column.key,
        header: column.header,
        cell: ({ getValue }) => formatCell(getValue()),
      })),
    [columns],
  )

  const globalFilterFn = useMemo(
    () => (row: Record<string, unknown>, query: string) =>
      columns.some((column) => formatCell(row[column.key]).toLowerCase().includes(query)),
    [columns],
  )

  return (
    <div className="my-2 max-w-2xl" data-testid="data-ui-data-table">
      {title ? <p className="mb-1.5 text-sm font-medium text-foreground">{title}</p> : null}
      <DataTable
        columns={columnDefs}
        data={rows}
        searchable={searchable}
        globalFilterFn={globalFilterFn}
        pageSize={10}
      />
    </div>
  )
}
