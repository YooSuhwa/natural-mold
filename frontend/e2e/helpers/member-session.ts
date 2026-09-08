import { randomUUID } from 'node:crypto'
import type { Page } from '@playwright/test'
import { z } from 'zod'

import { API_BASE, apiJson } from '../fixtures'

/** Keep profile mutations away from the shared suite login. The lane owns this DB. */
export async function registerMember(page: Page) {
  const body = await apiJson(
    await page.request.post(`${API_BASE}/api/auth/register`, {
      data: {
        email: `member-${randomUUID()}@moldy.dev`,
        name: 'E2E Member',
        password: 'E2E member password 42!',
      },
    }),
    'Register isolated member',
  )
  return z
    .object({
      csrf_token: z.string(),
      user: z.object({ id: z.string(), name: z.string(), is_super_user: z.literal(false) }),
    })
    .parse(body)
}
