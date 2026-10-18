import type { APIRequestContext } from '@playwright/test'

import { API_BASE, isRecord } from '../fixtures'

/** Shared API plumbing of the org-authz capture tour and its axe scan. */

export type Json = Record<string, unknown>

export interface ApiResult {
  status: number
  body: unknown
  etag: string | undefined
}

export function log(message: string): void {
  console.log(`[org-authz-tour] ${message}`)
}

export async function call(
  request: APIRequestContext,
  method: string,
  url: string,
  headers: Record<string, string>,
  data?: unknown,
): Promise<ApiResult> {
  const response = await request.fetch(`${API_BASE}${url}`, {
    method,
    headers,
    data: data === undefined ? undefined : data,
  })
  const text = await response.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    // keep text
  }
  if (!response.ok()) log(`${method} ${url} -> ${response.status()} ${text.slice(0, 300)}`)
  return { status: response.status(), body, etag: response.headers()['etag'] }
}

export function str(value: unknown, key: string): string | null {
  return isRecord(value) && typeof value[key] === 'string' ? (value[key] as string) : null
}

export async function orgVersion(request: APIRequestContext, orgId: string): Promise<string> {
  const res = await call(request, 'GET', `/api/orgs/${orgId}`, {})
  if (res.etag) return res.etag
  return `"${str(res.body, 'updated_at') ?? ''}"`
}

export async function tenantVersion(request: APIRequestContext, tenantId: string): Promise<string> {
  const res = await call(request, 'GET', `/api/tenants/${tenantId}`, {})
  if (res.etag) return res.etag
  const updated = str(res.body, 'updated_at') ?? ''
  return `"${updated.replace(/Z$/, '+00:00')}"`
}

/**
 * Wait until the UI is still: no running animations or transitions, no base-ui
 * starting/ending styles, for three consecutive 100ms polls (no fixed sleep).
 */
export async function settleUi(page: import('@playwright/test').Page): Promise<void> {
  await page
    .waitForFunction(
      () => {
        const busy =
          document.getAnimations().some((animation) => animation.playState === 'running') ||
          document.querySelector('[data-starting-style],[data-ending-style]') !== null
        const state = window as unknown as { __moldySettle?: number }
        state.__moldySettle = busy ? 0 : (state.__moldySettle ?? 0) + 1
        return state.__moldySettle >= 3
      },
      undefined,
      { polling: 100, timeout: 5_000 },
    )
    .catch(() => {})
  await page.evaluate(() => {
    ;(window as unknown as { __moldySettle?: number }).__moldySettle = 0
  })
}
