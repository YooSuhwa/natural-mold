import { render, screen } from '@testing-library/react'
import { QueryProvider } from '@/lib/providers/query-provider'
import { useQueryClient } from '@tanstack/react-query'

function TestChild() {
  const queryClient = useQueryClient()
  return <div>QC exists: {queryClient ? 'yes' : 'no'}</div>
}

describe('QueryProvider', () => {
  it('provides a QueryClient to children', () => {
    render(
      <QueryProvider>
        <TestChild />
      </QueryProvider>,
    )
    expect(screen.getByText('QC exists: yes')).toBeInTheDocument()
  })

  it('renders children correctly', () => {
    render(
      <QueryProvider>
        <div>Hello</div>
      </QueryProvider>,
    )
    expect(screen.getByText('Hello')).toBeInTheDocument()
  })
})
