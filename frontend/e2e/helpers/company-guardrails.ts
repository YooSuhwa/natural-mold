import type { APIRequestContext } from '@playwright/test'

import { API_BASE, apiGetJson, apiJson, isRecord, loginApi } from '../fixtures'

// The company guardrail policy is shared by every spec on the same backend. A spec that
// changes it saves it first and puts it back after, so the next spec (hitl-approval,
// document viewers) still sees the policy it was written for, whatever order they run in.

export async function companyPolicyUrl(request: APIRequestContext): Promise<string> {
  const rows = await apiGetJson(request, `${API_BASE}/api/me/organizations`)
  const list = Array.isArray(rows) ? rows.filter(isRecord) : []
  const home = list.find((row) => row.is_active === true) ?? list[0]
  if (!home || typeof home.tenant_id !== 'string') throw new Error('no organization')
  return `${API_BASE}/api/tenants/${home.tenant_id}/guardrails`
}

export type SavedCompanyPolicy = { url: string; body: Record<string, unknown> }

export async function saveCompanyPolicy(request: APIRequestContext): Promise<SavedCompanyPolicy> {
  await loginApi(request)
  const url = await companyPolicyUrl(request)
  const current = await apiGetJson(request, url)
  if (!isRecord(current)) throw new Error('guardrail policy is not an object')
  return {
    url,
    body: {
      mode: current.mode,
      default_template: current.default_template ?? null,
      locked_rules: current.locked_rules ?? [],
      tool_risk: current.tool_risk ?? {},
      always_ask_tools: current.always_ask_tools ?? [],
    },
  }
}

export async function restoreCompanyPolicy(
  request: APIRequestContext,
  saved: SavedCompanyPolicy,
): Promise<void> {
  const csrf = await loginApi(request) // a fresh login: afterAll has its own CSRF cookie
  const current = await apiGetJson(request, saved.url)
  const version = isRecord(current) ? String(current.updated_at) : ''
  await apiJson(
    await request.put(saved.url, {
      headers: { ...csrf, 'If-Match': `"${version.replace(/Z$/, '+00:00')}"` },
      data: saved.body,
    }),
    'restore guardrail policy',
  )
}
