import { test as base, expect, type APIRequestContext, type APIResponse } from '@playwright/test'

type ErrorCollector = {
  console: string[]
  page: string[]
  network: string[]
}

const E2E_USER = {
  id: '00000000-0000-4000-8000-000000000001',
  email: process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev',
  name: process.env.E2E_USER_NAME ?? process.env.E2E_NAME ?? 'E2E User',
  is_super_user: true,
  is_active: true,
  created_at: '2026-01-01T00:00:00.000Z',
  last_login_at: null,
}

export const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8001'
export const API_BASE = process.env.E2E_API_BASE_URL ?? `http://localhost:${BACKEND_PORT}`
export const E2E_EMAIL =
  process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
export const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

export type CsrfHeaders = Record<string, string>

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export async function failWithBody(label: string, response: APIResponse): Promise<never> {
  const body = await response.text().catch(() => '')
  throw new Error(`${label} failed (${response.status()}): ${body.slice(0, 800)}`)
}

export async function apiJson(response: APIResponse, label: string): Promise<unknown> {
  if (!response.ok()) {
    await failWithBody(label, response)
  }
  const body: unknown = await response.json()
  return body
}

export async function apiGetJson(request: APIRequestContext, url: string): Promise<unknown> {
  return apiJson(await request.get(url), `GET ${url}`)
}

export async function apiPostJson(
  request: APIRequestContext,
  url: string,
  csrfHeaders: CsrfHeaders,
  data?: Record<string, unknown>,
): Promise<unknown> {
  return apiJson(await request.post(url, { headers: csrfHeaders, data }), `POST ${url}`)
}

export async function apiDeleteOk(
  request: APIRequestContext,
  url: string,
  csrfHeaders: CsrfHeaders,
): Promise<void> {
  const response = await request.delete(url, { headers: csrfHeaders })
  if (!response.ok() && response.status() !== 404) {
    await failWithBody(`DELETE ${url}`, response)
  }
}

export async function loginApi(request: APIRequestContext): Promise<CsrfHeaders> {
  const body = await apiJson(
    await request.post(`${API_BASE}/api/auth/login`, {
      data: { email: E2E_EMAIL, password: E2E_PASSWORD },
    }),
    'E2E API login',
  )
  if (!isRecord(body) || typeof body.csrf_token !== 'string') {
    throw new Error('E2E API login did not return a CSRF token')
  }
  return { 'X-CSRF-Token': body.csrf_token }
}

function isKnownBenignConsoleError(text: string): boolean {
  return (
    text.includes('favicon') ||
    text.includes('Download the React DevTools') ||
    text.includes('React DevTools') ||
    text.includes('Failed to load resource: the server responded with a status of 404')
  )
}

function isExpectedNonOkResponse(url: string, status: number): boolean {
  return url.includes('favicon')
}

export const test = base.extend<{ authMock: void; errors: ErrorCollector }>({
  authMock: [
    async ({ page }, use) => {
      if (process.env.PW_SKIP_BACKEND === '1') {
        await page.route('**/api/auth/me', (route) => route.fulfill({ json: E2E_USER }))
      }
      await use()
    },
    { auto: true },
  ],
  errors: async ({ page }, use) => {
    const errors: ErrorCollector = { console: [], page: [], network: [] }

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text()
        if (!isKnownBenignConsoleError(text)) {
          errors.console.push(text)
        }
      }
    })
    page.on('pageerror', (err) => errors.page.push(err.message))
    page.on('response', (response) => {
      const status = response.status()
      const url = response.url()
      if (status >= 400 && !isExpectedNonOkResponse(url, status)) {
        errors.network.push(`${response.request().method()} ${url} ${status}`)
      }
    })
    page.on('requestfailed', (req) => {
      const url = req.url()
      const errorText = req.failure()?.errorText ?? 'unknown'
      const expectedStreamDetach =
        errorText.includes('net::ERR_ABORTED') &&
        /\/api\/conversations\/[^/]+\/(messages|messages\/resume|runs\/[^/]+\/stream(?:\?.*)?|langgraph\/threads\/[^/]+\/stream\/events)$/.test(
          url,
        )
      const expectedSdkCancelAbort =
        errorText.includes('net::ERR_ABORTED') &&
        req.method() === 'POST' &&
        /\/threads\/[^/]+\/runs\/[^/]+\/cancel(?:\?.*)?$/.test(url)
      const expectedRouteTransitionAbort =
        errorText.includes('net::ERR_ABORTED') &&
        req.method() === 'GET' &&
        (/\/api\/auth\/me$/.test(url) ||
          /\/api\/artifacts\/[^/]+\/content(?:\?.*)?$/.test(url) ||
          /\/api\/conversations\/[^/?]+(?:\?.*)?$/.test(url) ||
          /\/api\/conversations\/[^/]+\/artifacts(?:\?.*)?$/.test(url) ||
          /\/api\/conversations\/[^/]+\/langgraph\/threads\/[^/]+\/state(?:\?.*)?$/.test(url) ||
          /\/api\/agents\/[^/]+(?:$|\/conversations(?:\/page)?(?:\?.*)?$)/.test(url))
      const expectedLangGraphSdkTransitionAbort =
        errorText.includes('net::ERR_ABORTED') &&
        req.method() === 'POST' &&
        (/\/threads\/[^/]+\/history(?:\?.*)?$/.test(url) ||
          /\/api\/conversations\/[^/]+\/langgraph\/threads\/[^/]+\/commands(?:\?.*)?$/.test(url))
      const expectedBranchSwitchAbort =
        errorText.includes('net::ERR_ABORTED') &&
        req.method() === 'POST' &&
        /\/api\/conversations\/[^/]+\/messages\/switch-branch(?:\?.*)?$/.test(url)
      // Owner DELETE on a conversation-scoped resource (deleting the conversation,
      // or revoking its share link): the server completes the DELETE (confirmed by
      // a follow-up GET that returns 404), but the in-flight client request is
      // observed to abort around completion — a benign transport artifact,
      // mirroring the GET route-transition aborts. The functional outcome is still
      // asserted server-side via the 404 poll, so this only suppresses the
      // transport-level ERR_ABORTED, never a real 4xx/5xx (those surface via the
      // response handler above).
      const expectedConversationDeleteAbort =
        errorText.includes('net::ERR_ABORTED') &&
        req.method() === 'DELETE' &&
        /\/api\/conversations\/[^/?]+(?:\/share)?(?:\?.*)?$/.test(url)
      if (
        !url.includes('favicon') &&
        !expectedStreamDetach &&
        !expectedSdkCancelAbort &&
        !expectedRouteTransitionAbort &&
        !expectedLangGraphSdkTransitionAbort &&
        !expectedBranchSwitchAbort &&
        !expectedConversationDeleteAbort
      ) {
        errors.network.push(`${req.method()} ${url} ${errorText}`)
      }
    })

    await use(errors)

    // Auto-verify: no JS exceptions after each test
    expect(errors.page, 'JS exceptions detected').toEqual([])
  },
})

export { expect }
