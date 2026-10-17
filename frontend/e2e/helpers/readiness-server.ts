import type { Page, Route } from '@playwright/test'

/**
 * In-memory side of the S21/S23 scenarios (plan 16.5 E2E #4/#5): one per-user MCP server
 * the caller must connect, one credential of the accepted type, and the binding flag.
 * Org-authz enforce fixtures (16.5 role users + OpenFGA) do not exist yet, so the
 * readiness, credential and binding routes are played over `page.route`.
 */
export const SERVER_ID = '00000000-0000-4000-8000-0000000000d1'
export const CREDENTIAL_ID = '00000000-0000-4000-8000-0000000000d2'

export function readinessServer() {
  const state = { bound: false, bindCalls: 0 }

  function readiness() {
    return {
      overall: state.bound ? 'ready' : 'needs_connection',
      identity_mode: 'per_user',
      items: [
        { kind: 'llm_model', required: true, result: 'ready', count: 1 },
        state.bound
          ? { kind: 'mcp_server', id: SERVER_ID, name: 'Slack', required: true, result: 'ready' }
          : {
              kind: 'mcp_server',
              id: SERVER_ID,
              name: 'Slack',
              required: true,
              result: 'connect_by_me',
              credential: { mode: 'per_user', status: 'missing', definition_key: 'http_bearer' },
              action: { type: 'connect' },
            },
      ],
    }
  }

  async function bind(route: Route) {
    state.bound = true
    state.bindCalls += 1
    await route.fulfill({
      json: {
        id: 'binding-1',
        resource_type: 'mcp_server',
        resource_id: SERVER_ID,
        credential_id: CREDENTIAL_ID,
      },
    })
  }

  async function install(page: Page, agentId: string) {
    await page.route(`**/api/agents/${agentId}/readiness**`, (route) =>
      route.fulfill({ json: readiness() }),
    )
    await page.route('**/api/credentials', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({
            json: [
              {
                id: CREDENTIAL_ID,
                user_id: 'me',
                definition_key: 'http_bearer',
                name: '내 Slack',
                field_keys: ['token'],
                is_shared: false,
                is_system: false,
                status: 'active',
                key_id: 'k1',
                last_used_at: null,
                last_tested_at: null,
                last_test_result: null,
                created_at: '2026-10-01T00:00:00Z',
                updated_at: '2026-10-01T00:00:00Z',
              },
            ],
          })
        : route.fallback(),
    )
    await page.route(`**/api/credential-bindings/mcp_server/${SERVER_ID}`, (route) =>
      route.request().method() === 'PUT' ? bind(route) : route.fallback(),
    )
  }

  return { state, install, readiness }
}
