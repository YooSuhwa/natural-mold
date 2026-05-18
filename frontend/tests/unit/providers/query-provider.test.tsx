import { render, screen } from '@testing-library/react'
import { useQueryClient } from '@tanstack/react-query'
import { vi } from 'vitest'

// next/navigation is stubbed globally in tests/setup.ts. next-intl is
// only mocked here because the QueryProvider's toast wiring is the sole
// consumer in the unit-test bundle.
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

import { QueryProvider } from '@/lib/providers/query-provider'

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
