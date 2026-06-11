import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'

import { useInstallItem, useMarketplaceItemsPage } from '@/lib/hooks/use-marketplace'
import { server } from '../../setup'
import { mockMarketplaceItemsPage } from '../../mocks/fixtures'

const API_BASE = 'http://localhost:8001'

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function createWrapper(queryClient = createQueryClient()) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
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

describe('useInstallItem', () => {
  it('invalidates MCP server list after marketplace install', async () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(['mcp-servers'], [{ id: 'mcp-1' }])

    server.use(
      http.post(`${API_BASE}/api/marketplace/items/:itemId/install`, () =>
        HttpResponse.json({
          id: 'install-1',
          item_id: 'item-1',
          version_id: 'version-1',
          resource_type: 'mcp',
          installed_skill_id: null,
          installed_agent_id: null,
          installed_agent_blueprint_id: null,
          installed_mcp_server_id: 'mcp-1',
          install_status: 'active',
          is_dirty: false,
          installed_at: '2026-06-11T00:00:00',
          updated_at: '2026-06-11T00:00:00',
        }),
      ),
    )

    const { result } = renderHook(() => useInstallItem('item-1'), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({})
    })

    expect(queryClient.getQueryState(['mcp-servers'])?.isInvalidated).toBe(true)
  })
})
