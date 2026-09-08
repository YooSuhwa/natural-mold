import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { marketplaceApi } from '@/lib/api/marketplace'
import { useDisableItem, useModerationQueue } from '@/lib/hooks/use-marketplace'
import { marketplaceQueryKeys } from '@/lib/query-keys/marketplace'
import type { MarketplaceItem, MarketplaceStatus } from '@/lib/types/marketplace'

vi.mock('@/lib/api/marketplace', () => ({
  marketplaceApi: { moderationQueue: vi.fn(), disableItem: vi.fn() },
}))

function item(status: MarketplaceStatus): MarketplaceItem {
  return {
    id: status,
    resource_type: 'mcp',
    name: status,
    slug: status,
    description: null,
    visibility: 'public',
    status,
    is_system: false,
    is_listed: false,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
    credential_summary: {
      status: 'none',
      required_count: 0,
      optional_count: 0,
      missing_required_count: 0,
    },
    publication_summary: {
      state: 'published_public_unlisted',
      is_listed: false,
      shared_user_count: 0,
    },
    installation: { installed: false, update_available: false, dirty: false },
  }
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return { client, wrapper: Wrapper }
}

describe('marketplace moderation queue', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('shows only published candidates while retaining raw owner-visible query data', async () => {
    const rows = [item('published'), item('draft'), item('deprecated'), item('disabled')]
    vi.mocked(marketplaceApi.moderationQueue).mockResolvedValue(rows)
    const { client, wrapper } = setup()
    const { result } = renderHook(() => useModerationQueue(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([rows[0]])
    expect(client.getQueryData(marketplaceQueryKeys.moderation)).toEqual(rows)
    client.clear()
  })

  it('removes a disabled item after the existing mutation invalidates and refetches', async () => {
    const published = item('published')
    const disabled = { ...published, status: 'disabled' } satisfies MarketplaceItem
    vi.mocked(marketplaceApi.moderationQueue)
      .mockResolvedValueOnce([published])
      .mockResolvedValue([disabled])
    vi.mocked(marketplaceApi.disableItem).mockResolvedValue(disabled)
    const { client, wrapper } = setup()
    const { result } = renderHook(
      () => ({ queue: useModerationQueue(), disable: useDisableItem() }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.queue.data).toEqual([published]))
    await act(async () => {
      await result.current.disable.mutateAsync(published.id)
    })
    await waitFor(() => expect(result.current.queue.data).toEqual([]))
    expect(marketplaceApi.moderationQueue).toHaveBeenCalledTimes(2)
    client.clear()
  })

  it('does not request the operator queue for a disabled observer', () => {
    const { client, wrapper } = setup()
    const { result } = renderHook(() => useModerationQueue(false), { wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(marketplaceApi.moderationQueue).not.toHaveBeenCalled()
    client.clear()
  })
})
