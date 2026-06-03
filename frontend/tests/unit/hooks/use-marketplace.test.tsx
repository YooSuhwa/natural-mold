import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { useMarketplaceItemsPage } from '@/lib/hooks/use-marketplace'
import { mockMarketplaceItemsPage } from '../../mocks/fixtures'

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
  return Wrapper
}

describe('useMarketplaceItemsPage', () => {
  it('fetches a paginated marketplace envelope', async () => {
    const { result } = renderHook(
      () => useMarketplaceItemsPage({ resource_type: 'skill', limit: 24 }),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockMarketplaceItemsPage)
  })
})
