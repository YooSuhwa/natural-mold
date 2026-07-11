'use client'

import { useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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
  /**
   * Controlled selection state. Pass together with
   * `onRowSelectionStateChange` when the parent needs to reset/own the
   * selection (e.g. clear after a bulk action) without remounting the table —
   * a key-remount would also wipe sorting and the page index.
   */
  rowSelectionState?: RowSelectionState
  onRowSelectionStateChange?: (
    updater: RowSelectionState | ((previous: RowSelectionState) => RowSelectionState),
  ) => void
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
  rowSelectionState,
  onRowSelectionStateChange,
  getRowId,
  toolbar,
}: DataTableProps<T>) {
  const t = useTranslations('common.dataTable')
  const resolvedEmptyTitle = emptyTitle ?? t('emptyDefault')
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [search, setSearch] = useState('')
  const [internalRowSelection, setInternalRowSelection] = useState<RowSelectionState>({})
  const rowSelection = rowSelectionState ?? internalRowSelection
  const setRowSelection = onRowSelectionStateChange ?? setInternalRowSelection
  const deferredSearch = useDeferredValue(search)
  const normalizedSearch = useMemo(() => deferredSearch.trim().toLowerCase(), [deferredSearch])

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
            onCheckedChange={(value) => table.toggleAllPageRowsSelected(Boolean(value))}
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
    // 데이터 갱신(refetch/삭제)마다 1페이지로 튕기지 않는다 — controlled
    // selection을 도입한 사유("정렬·페이지 유지")와 동일 계약. 범위 밖으로
    // 밀려난 pageIndex는 아래 effect가 마지막 페이지로 클램프한다.
    autoResetPageIndex: false,
    initialState: { pagination: { pageSize } },
  })

  useEffect(() => {
    const pageCount = table.getPageCount()
    const pageIndex = table.getState().pagination.pageIndex
    if (pageIndex > 0 && pageIndex >= pageCount) {
      table.setPageIndex(Math.max(0, pageCount - 1))
    }
    // filtered가 pagination 입력의 전부 — table 인스턴스는 안정적이다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, pageSize])

  // Notify parent when the selection changes. We map back to the source rows
  // so callers receive the original objects (not table-wrapper rows).
  // `filtered` is a dep on purpose: when the parent swaps/shrinks `data`
  // (external search/filter), selected keys can point at rows no longer in
  // the model — without re-running, the parent would keep a stale selection
  // (bulk bar count/targets diverge from visible checkboxes).
  // The row-id signature guard makes the effect convergent: parents that pass
  // unstable `data` identities would otherwise loop (notify → parent setState
  // → new data → notify …, "Maximum update depth exceeded"). NOTE the guard
  // is id-based: same ids with refreshed row objects do NOT re-notify — treat
  // the callback payload as "which rows", and derive fresh objects from your
  // current data at action time (store ids, not object snapshots).
  // 반쪽 controlled 결합은 조용히 죽는다 — 개발 모드에서 즉시 경고.
  if (
    process.env.NODE_ENV !== 'production' &&
    (rowSelectionState === undefined) !== (onRowSelectionStateChange === undefined)
  ) {
    console.warn(
      'DataTable: rowSelectionState and onRowSelectionStateChange must be passed together.',
    )
  }

  // 데이터에서 빠진 행(외부 검색/필터/삭제)의 선택 키를 정리한다 — 남겨두면
  // 사용자가 "모두 해제"한 뒤 필터를 풀 때 유령 선택이 부활해 벌크 대상으로
  // 재등장한다. 정리 후 아래 통지 effect가 시그니처 변화로 부모에 반영한다.
  useEffect(() => {
    if (!enableRowSelection) return
    const validIds = new Set(table.getCoreRowModel().rows.map((row) => row.id))
    const staleKeys = Object.keys(rowSelection).filter((key) => !validIds.has(key))
    if (staleKeys.length === 0) return
    setRowSelection((previous) => {
      const next = { ...previous }
      for (const key of staleKeys) delete next[key]
      return next
    })
    // rowSelection/filtered가 유효성 입력의 전부 — table·setter는 안정적이다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowSelection, filtered, enableRowSelection])

  const lastSelectionSignature = useRef('')
  useEffect(() => {
    if (!enableRowSelection || !onRowSelectionChange) return
    const selectedRows = table.getSelectedRowModel().rows
    const signature = selectedRows.map((r) => r.id).join('\u0000')
    if (signature === lastSelectionSignature.current) return
    lastSelectionSignature.current = signature
    onRowSelectionChange(selectedRows.map((r) => r.original))
    // We depend on rowSelection + filtered (the inputs of the row model) — table is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowSelection, filtered, enableRowSelection])

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
              value={(table.getColumn(filter.columnId)?.getFilterValue() as string) ?? ''}
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
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          <ArrowUpDown className="size-3 text-muted-foreground" />
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
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
                  onClick={
                    onRowClick
                      ? (event) => {
                          // 셀 안의 인터랙티브 요소(체크박스/버튼/메뉴/링크)
                          // 클릭은 행 내비게이션으로 승격하지 않는다 — 일부
                          // 프리미티브는 자식에서 클릭이 시작돼 셀 단위
                          // stopPropagation만으로는 새지 않는다고 보장 못 한다.
                          const target = event.target as HTMLElement
                          if (
                            target.closest(
                              'button, a, input, select, textarea, label, ' +
                                '[role="checkbox"], [role="switch"], [role="combobox"], ' +
                                '[role="menu"], [role="menuitem"], [role="menuitemcheckbox"], ' +
                                '[role="option"]',
                            )
                          ) {
                            return
                          }
                          onRowClick(row.original)
                        }
                      : undefined
                  }
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
    <Select value={value || 'all'} onValueChange={(v) => v !== null && onValueChange(v)}>
      <SelectTrigger className="h-8 w-40" aria-label={filter.label}>
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
