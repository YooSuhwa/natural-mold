import { describe, expect, it } from 'vitest'

import { marketplaceApi } from '@/lib/api/marketplace'
import { mockMarketplaceItemsPage } from '../../mocks/fixtures'

describe('marketplaceApi', () => {
  it('page() returns a paginated marketplace envelope', async () => {
    const page = await marketplaceApi.page({
      resource_type: 'skill',
      q: 'image',
      limit: 24,
      offset: 0,
    })

    expect(page).toEqual(mockMarketplaceItemsPage)
    expect(page.items).toHaveLength(2)
  })
})
