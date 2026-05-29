import type { ColumnDef } from '@tanstack/react-table'
import { render, screen, userEvent } from '../../test-utils'
import { DataTable } from '@/components/ui/data-table'

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
})
