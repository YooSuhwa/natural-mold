'use client'

import { useState, type ReactNode } from 'react'
import {
  type ColumnDef,
  type ColumnFiltersState,
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
  emptyTitle = 'No items',
  emptyDescription,
  emptyAction,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [search, setSearch] = useState('')

  const filtered = search
    ? data.filter((row) => {
        if (globalFilterFn) return globalFilterFn(row, search.toLowerCase())
        const r = row as unknown as Record<string, unknown>
        const name = String(r.name ?? '').toLowerCase()
        const description = String(r.description ?? '').toLowerCase()
        const q = search.toLowerCase()
        return name.includes(q) || description.includes(q)
      })
    : data

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  })

  return (
    <div className="space-y-3">
      {(searchable || filters?.length) && (
        <div className="flex flex-wrap items-center gap-2">
          {searchable && (
            <SearchInput
              containerClassName="w-full sm:w-72"
              placeholder={searchPlaceholder ?? 'Search...'}
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
        </div>
      )}

      <div className="rounded-lg border">
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
                  {columns.map((_col, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="p-0">
                  <EmptyState
                    title={emptyTitle}
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
            Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()} ·{' '}
            {table.getFilteredRowModel().rows.length} item
            {table.getFilteredRowModel().rows.length === 1 ? '' : 's'}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              <ChevronLeft className="size-4" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              Next
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
  return (
    <Select
      value={value || 'all'}
      onValueChange={(v) => v !== null && onValueChange(v)}
    >
      <SelectTrigger className="h-8 w-[160px]" aria-label={filter.label}>
        <SelectValue placeholder={filter.label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">All {filter.label}</SelectItem>
        {filter.options.map((opt) => (
          <SelectItem key={opt.value} value={opt.value}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
