import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { z } from 'zod'

import { API_BASE, apiGetJson, apiJson, apiPostJson, type CsrfHeaders } from '../fixtures'

interface MemberInvitation {
  readonly email: string
  readonly token: string
}

/** Keep profile mutations away from the shared suite login. The lane owns this DB. */
export async function registerMember(page: Page, invitation?: MemberInvitation) {
  const body = await apiJson(
    await page.request.post(`${API_BASE}/api/auth/register`, {
      data: {
        email: invitation?.email ?? `member-${randomUUID()}@moldy.dev`,
        name: 'E2E Member',
        password: 'E2E member password 42!',
        ...(invitation ? { invitation_token: invitation.token } : {}),
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

/**
 * Register a member into the inviter's active organization. A plain signup joins the default
 * company, which is not the seeded suite login's company, and company-scoped reads hide it.
 */
export async function registerOrgMember(
  page: Page,
  inviter: APIRequestContext,
  inviterCsrf: CsrfHeaders,
) {
  const organizations = z
    .array(z.object({ id: z.string(), is_active: z.boolean() }))
    .parse(await apiGetJson(inviter, `${API_BASE}/api/me/organizations`))
  const active = organizations.find((organization) => organization.is_active)
  if (!active) throw new Error('The inviter has no active organization')
  const email = `member-${randomUUID()}@moldy.dev`
  const { token } = z.object({ token: z.string() }).parse(
    await apiPostJson(inviter, `${API_BASE}/api/orgs/${active.id}/invitations`, inviterCsrf, {
      email,
      role: 'member',
    }),
  )
  return registerMember(page, { email, token })
}
