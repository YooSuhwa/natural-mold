import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { DataTableCard } from '../data-table-card'

describe('DataTableCard', () => {
  it('renders headers, cell values, and the title', () => {
    render(
      <DataTableCard
        title="팀 점수"
        columns={[
          { key: 'name', header: '이름' },
          { key: 'score', header: '점수' },
        ]}
        rows={[
          { name: 'Alice', score: 92 },
          { name: 'Bob', score: 88 },
        ]}
      />,
    )

    expect(screen.getByTestId('data-ui-data-table')).toBeInTheDocument()
    expect(screen.getByText('팀 점수')).toBeInTheDocument()
    expect(screen.getByText('이름')).toBeInTheDocument()
    expect(screen.getByText('점수')).toBeInTheDocument()
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('92')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
  })

  it('renders object cell values as JSON text (no raw HTML)', () => {
    render(
      <DataTableCard columns={[{ key: 'meta', header: 'Meta' }]} rows={[{ meta: { a: 1 } }]} />,
    )
    expect(screen.getByText('{"a":1}')).toBeInTheDocument()
  })
})
