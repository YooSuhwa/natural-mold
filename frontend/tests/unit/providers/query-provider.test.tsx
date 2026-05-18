import { render, screen } from '@testing-library/react'
import { useQueryClient } from '@tanstack/react-query'
import { vi } from 'vitest'

// QueryProvider depends on next/navigation's AppRouter context + next-intl's
// IntlProvider. In a bare jsdom run neither is mounted, so we stub the two
// hooks at module-resolution time. The fakes are deliberately minimal —
// the goal is to keep the integration with TanStack Query honest while
// the auth-router side-effects (toast + redirect) are exercised by the
// dedicated session-gate tests.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))
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
