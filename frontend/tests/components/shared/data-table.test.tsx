import type { ColumnDef } from '@tanstack/react-table'
import { render, screen, userEvent } from '../../test-utils'
import { DataTable } from '@/components/ui/data-table'

// FilterDef(Radix Select) 상호작용용 경량 mock — jsdom에서 실제 Radix Select는
// 포인터 캡처 의존으로 불안정하다 (models 페이지 테스트 선례).
let lastOnValueChange: ((value: string) => void) | undefined
vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    onValueChange,
  }: {
    children: React.ReactNode
    onValueChange?: (value: string) => void
  }) => {
    lastOnValueChange = onValueChange
    return <div>{children}</div>
  },
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
  SelectValue: ({ placeholder }: { placeholder?: string }) => <span>{placeholder}</span>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <button type="button" data-value={value} onClick={() => lastOnValueChange?.(value)}>
      {children}
    </button>
  ),
}))

interface Row {
  id: string
  name: string
}

const columns: ColumnDef<Row>[] = [
  {
    accessorKey: 'name',
    header: '이름',
    cell: ({ row }) => row.original.name,
  },
]

describe('DataTable', () => {
  it('uses Korean default empty text', () => {
    render(<DataTable columns={columns} data={[]} />)

    expect(screen.getByText('항목이 없어요')).toBeInTheDocument()
  })

  it('uses Korean pagination labels', () => {
    render(
      <DataTable
        columns={columns}
        data={[
          { id: '1', name: '첫 번째' },
          { id: '2', name: '두 번째' },
        ]}
        pageSize={1}
      />,
    )

    expect(screen.getByText('1페이지 / 2페이지 · 2개 항목')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /이전/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /다음/ })).toBeInTheDocument()
  })

  it('does not recompute global search results on same-prop rerenders', async () => {
    const data = [
      { id: '1', name: '첫 번째' },
      { id: '2', name: '두 번째' },
    ]
    const filterFn = vi.fn((row: Row, query: string) =>
      row.name.toLowerCase().includes(query),
    )

    const { rerender } = render(
      <DataTable
        columns={columns}
        data={data}
        searchable
        globalFilterFn={filterFn}
      />,
    )

    await userEvent.type(screen.getByPlaceholderText('검색...'), '첫')
    const callsAfterSearch = filterFn.mock.calls.length

    rerender(
      <DataTable
        columns={columns}
        data={data}
        searchable
        globalFilterFn={filterFn}
      />,
    )

    expect(filterFn).toHaveBeenCalledTimes(callsAfterSearch)
  })

  // R5 회귀 3종 — rowSelection prune/pageIndex clamp effect의 자기 파괴 방지.

  it('loading 플리커(쿼리 키 변경으로 data가 잠시 []) 동안 선택을 지우지 않는다', () => {
    const rows = [
      { id: '1', name: '첫 번째' },
      { id: '2', name: '두 번째' },
    ]
    const onStateChange = vi.fn()
    const controlled = {
      columns,
      enableRowSelection: true,
      rowSelectionState: { '1': true },
      onRowSelectionStateChange: onStateChange,
    }
    const { rerender } = render(<DataTable {...controlled} data={rows} />)

    // 검색 키 입력/kind 탭 전환을 흉내: data가 빈 배열 + loading=true.
    rerender(<DataTable {...controlled} data={[]} loading />)
    expect(onStateChange).not.toHaveBeenCalled()

    // 로딩이 끝나고 행이 진짜 사라졌을 때만 prune이 동작한다.
    rerender(<DataTable {...controlled} data={[rows[1]]} loading={false} />)
    expect(onStateChange).toHaveBeenCalled()
  })

  it('내부 검색으로 가려진 선택 행은 prune되지 않는다', async () => {
    const rows = [
      { id: '1', name: '첫 번째' },
      { id: '2', name: '두 번째' },
    ]
    const onStateChange = vi.fn()
    render(
      <DataTable
        columns={columns}
        data={rows}
        searchable
        enableRowSelection
        rowSelectionState={{ '1': true }}
        onRowSelectionStateChange={onStateChange}
      />,
    )

    // '첫 번째'(선택됨)를 가리는 검색 — 선택 키는 data prop 기준으로 유효하다.
    await userEvent.type(screen.getByPlaceholderText('검색...'), '두')
    expect(screen.queryByText('첫 번째')).not.toBeInTheDocument()
    expect(onStateChange).not.toHaveBeenCalled()
  })

  it('컬럼 필터로 줄어든 표는 범위 밖 페이지에 좌초하지 않는다', async () => {
    interface TypedRow extends Row {
      type: string
    }
    const typedColumns: ColumnDef<TypedRow>[] = [
      { accessorKey: 'name', header: '이름', cell: ({ row }) => row.original.name },
      { accessorKey: 'type', header: '종류', cell: ({ row }) => row.original.type },
    ]
    const rows: TypedRow[] = [
      { id: '1', name: 'A행', type: 'x' },
      { id: '2', name: 'B행', type: 'x' },
      { id: '3', name: 'C행', type: 'y' },
    ]
    render(
      <DataTable
        columns={typedColumns}
        data={rows}
        pageSize={1}
        filters={[
          { columnId: 'type', label: '종류', options: [{ value: 'x', label: 'X만' }] },
        ]}
      />,
    )

    // 3페이지(C행)로 이동 후 필터로 2행(x)만 남긴다.
    await userEvent.click(screen.getByRole('button', { name: /다음/ }))
    await userEvent.click(screen.getByRole('button', { name: /다음/ }))
    expect(screen.getByText('C행')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'X만' }))

    // 클램프가 없으면 pageIndex=2가 pageCount=2 밖에 남아 빈 바디 + 페이지네이션
    // 숨김의 dead-end가 된다 — 마지막 유효 페이지(B행)로 수렴해야 한다.
    expect(screen.getByText('B행')).toBeInTheDocument()
    expect(screen.getByText('2페이지 / 2페이지 · 2개 항목')).toBeInTheDocument()
  })
})
