'use client'

import { useDeferredValue, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import {
  type ColumnDef,
  type ColumnFiltersState,
  type RowSelectionState,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { SearchInput } from '@/components/shared/search-input'
import { EmptyState } from '@/components/shared/empty-state'

export interface FilterDef {
  /** column id (must match a column.accessorKey/id in the columns array) */
  columnId: string
  label: string
  options: Array<{ value: string; label: string }>
}

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  data: T[]
  searchable?: boolean
  searchPlaceholder?: string
  /**
   * Custom global filter function. Defaults to substring-match across the
   * `name` and `description` fields if present.
   */
  globalFilterFn?: (row: T, query: string) => boolean
  filters?: FilterDef[]
  onRowClick?: (row: T) => void
  pageSize?: number
  loading?: boolean
  emptyTitle?: string
  emptyDescription?: string
  emptyAction?: ReactNode
  /**
   * When true, the leading column becomes a per-row checkbox and the header
   * gets a select-all checkbox. The parent receives selection updates via
   * `onRowSelectionChange`.
   */
  enableRowSelection?: boolean
  onRowSelectionChange?: (rows: T[]) => void
  /** Stable row identifier for selection state. Defaults to `row.id`. */
  getRowId?: (row: T, index: number) => string
  /**
   * Optional toolbar slot rendered on the right of the search/filter row.
   * Use it to render bulk actions ("Test Selected", "Delete selected"...).
   */
  toolbar?: ReactNode
}

export function DataTable<T>({
  columns,
  data,
  searchable = false,
  searchPlaceholder,
  globalFilterFn,
  filters,
  onRowClick,
  pageSize = 10,
  loading = false,
  emptyTitle,
  emptyDescription,
  emptyAction,
  enableRowSelection = false,
  onRowSelectionChange,
  getRowId,
  toolbar,
}: DataTableProps<T>) {
  const t = useTranslations('common.dataTable')
  const resolvedEmptyTitle = emptyTitle ?? t('emptyDefault')
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [search, setSearch] = useState('')
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const deferredSearch = useDeferredValue(search)
  const normalizedSearch = useMemo(
    () => deferredSearch.trim().toLowerCase(),
    [deferredSearch],
  )

  const filtered = useMemo(() => {
    if (!normalizedSearch) return data

    return data.filter((row) => {
      if (globalFilterFn) return globalFilterFn(row, normalizedSearch)
      const r = row as unknown as Record<string, unknown>
      const name = String(r.name ?? '').toLowerCase()
      const description = String(r.description ?? '').toLowerCase()
      return name.includes(normalizedSearch) || description.includes(normalizedSearch)
    })
  }, [data, globalFilterFn, normalizedSearch])

  // Inject the leading selection column when requested. Wrapped in a separate
  // const so the original `columns` prop is preserved for downstream use.
  const tableColumns = useMemo(() => {
    if (!enableRowSelection) return columns

    return [
      {
        id: '__select',
        header: ({ table }) => (
          <Checkbox
            aria-label={t('selectAll')}
            checked={table.getIsAllPageRowsSelected()}
            indeterminate={table.getIsSomePageRowsSelected()}
            onCheckedChange={(value) =>
              table.toggleAllPageRowsSelected(Boolean(value))
            }
            onClick={(e) => e.stopPropagation()}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            aria-label={t('selectRow')}
            checked={row.getIsSelected()}
            disabled={!row.getCanSelect()}
            onCheckedChange={(value) => row.toggleSelected(Boolean(value))}
            onClick={(e) => e.stopPropagation()}
          />
        ),
        enableSorting: false,
        size: 32,
      } as ColumnDef<T, unknown>,
      ...columns,
    ] as ColumnDef<T, unknown>[]
  }, [columns, enableRowSelection, t])

  const table = useReactTable({
    data: filtered,
    columns: tableColumns,
    state: { sorting, columnFilters, rowSelection },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onRowSelectionChange: setRowSelection,
    enableRowSelection,
    getRowId: getRowId
      ? (row, index) => getRowId(row, index)
      : (row, index) => {
          const r = row as unknown as { id?: string }
          return r.id ?? String(index)
        },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  })

  // Notify parent when the selection changes. We map back to the source rows
  // so callers receive the original objects (not table-wrapper rows).
  useEffect(() => {
    if (!enableRowSelection || !onRowSelectionChange) return
    const selectedRows = table
      .getSelectedRowModel()
      .rows.map((r) => r.original)
    onRowSelectionChange(selectedRows)
    // We depend on rowSelection (the actual key map) — table is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowSelection, enableRowSelection])

  return (
    <div className="space-y-3">
      {(searchable || filters?.length || toolbar) && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/70 bg-card/70 p-3">
          {searchable && (
            <SearchInput
              containerClassName="w-full sm:w-72"
              placeholder={searchPlaceholder ?? t('searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
          {filters?.map((filter) => (
            <DataTableFilter
              key={filter.columnId}
              filter={filter}
              value={
                (table.getColumn(filter.columnId)?.getFilterValue() as string) ?? ''
              }
              onValueChange={(value) =>
                table
                  .getColumn(filter.columnId)
                  ?.setFilterValue(value === 'all' ? undefined : value)
              }
            />
          ))}
          {toolbar && <div className="ml-auto flex items-center gap-2">{toolbar}</div>}
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-border/70 bg-card/90 shadow-[var(--moldy-shadow-card)]">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const sortable = header.column.getCanSort()
                  return (
                    <TableHead key={header.id}>
                      {header.isPlaceholder ? null : sortable ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="inline-flex items-center gap-1 text-left font-medium"
                        >
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                          <ArrowUpDown className="size-3 text-muted-foreground" />
                        </button>
                      ) : (
                        flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )
                      )}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <TableRow key={`skeleton-${i}`}>
                  {tableColumns.map((_col, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={tableColumns.length} className="p-0">
                  <EmptyState
                    title={resolvedEmptyTitle}
                    description={emptyDescription}
                    action={emptyAction}
                    className="border-0"
                  />
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-clickable={onRowClick ? '' : undefined}
                  className={onRowClick ? 'cursor-pointer' : undefined}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {table.getPageCount() > 1 && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {t('pagination', {
              page: table.getState().pagination.pageIndex + 1,
              totalPages: table.getPageCount(),
              count: table.getFilteredRowModel().rows.length,
            })}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              <ChevronLeft className="size-4" />
              {t('previous')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              {t('next')}
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function DataTableFilter({
  filter,
  value,
  onValueChange,
}: {
  filter: FilterDef
  value: string
  onValueChange: (value: string) => void
}) {
  const t = useTranslations('common.dataTable')
  return (
    <Select
      value={value || 'all'}
      onValueChange={(v) => v !== null && onValueChange(v)}
    >
      <SelectTrigger className="h-8 w-[160px]" aria-label={filter.label}>
        <SelectValue placeholder={filter.label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{t('allFilter', { label: filter.label })}</SelectItem>
        {filter.options.map((opt) => (
          <SelectItem key={opt.value} value={opt.value}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
