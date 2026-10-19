import type { Route } from '@playwright/test'

import { settle } from '../captures/_capture-helpers'
import { readinessServer } from './readiness-server'
import { call, settleUi, str } from './org-authz-api'
import type { TourContext } from './org-authz-tour'

const AUTH_CREDENTIAL_ID = '33333333-3333-4333-8333-333333333333'

function requireId(id: string | null, what: string): string {
  if (!id) throw new Error(`no ${what} seeded`)
  return id
}

/** E-04 share dialogs, G-04 readiness, G-05 auth_required card (route-played). */
export async function sharingSteps(t: TourContext): Promise<void> {
  const { page, seed: s, dialog } = t
  await t.step('e04-share', async () => {
    await t.visit(`/agents/${requireId(s.agentId, 'agent')}/settings`)
    await page.getByRole('button', { name: '사내 IT 헬프데스크 공유 설정 열기' }).click()
    await dialog().waitFor()
    await settle(page, 1_000)
    await t.screen('31-e04-agent-share-dialog.png')
    await dialog().getByRole('button', { name: '소유권 이전' }).first().click()
    await settleUi(page)
    await t.screen('32-e04-agent-share-transfer-section.png')
  })
  await t.step('e04-marketplace-share', async () => {
    await t.visit(`/marketplace/${requireId(s.marketplaceItemId, 'marketplace item')}`)
    await t.screen('33-e04-marketplace-item-restricted.png', { fullPage: true })
    await page.getByRole('button', { name: '대상 관리' }).first().click()
    await dialog().waitFor()
    await settle(page, 800)
    await t.screen('34-e04-marketplace-share-dialog.png')
  })
  await t.step('g04-badge', async () => {
    await page.route('**/api/agents/readiness-summary', async (route: Route) => {
      const body = route.request().postDataJSON() as { agent_ids?: string[] } | null
      const json: Record<string, unknown> = {}
      ;(body?.agent_ids ?? []).forEach((id, index) => {
        json[id] =
          index % 3 === 0
            ? { overall: 'needs_connection', counts: { connect_by_me: 1 } }
            : index % 3 === 1
              ? { overall: 'limited', counts: { no_permission: 2 } }
              : { overall: 'blocked', counts: { owner_must_connect: 1 } }
      })
      await route.fulfill({ json })
    })
    await t.visit('/')
    await settleUi(page)
    await t.screen('35-g04-readiness-badges-dashboard.png')
  })
  await t.step('g04-panel', async () => {
    const agentId = requireId(s.agentId, 'agent')
    await readinessServer().install(page, agentId)
    await t.visit(`/agents/${agentId}/conversations/new`)
    await page.getByText('필수 항목 1개 연결이 필요해요').waitFor({ timeout: 60_000 })
    await settleUi(page)
    await t.screen('36-g04-readiness-collapsed-card.png')
    await page.getByRole('button', { name: '점검 보기' }).click()
    await dialog().waitFor()
    await settleUi(page)
    await t.screen('37-g04-readiness-check-panel.png')
    await dialog().getByRole('button', { name: '연결하기' }).click()
    await settleUi(page)
    await t.screen('38-g04-credential-binding-dialog.png')
  })
  await t.step('g05-auth-card', async () => {
    const agentId = requireId(s.agentId, 'agent')
    const convo = await call(t.request, 'POST', `/api/agents/${agentId}/conversations`, t.csrf, {
      title: 'Slack 공지 올리기',
    })
    const conversationId = requireId(str(convo.body, 'id'), 'conversation')
    const interrupt = {
      id: 'intr-auth-oauth',
      value: {
        type: 'auth_required',
        reason: 'expired',
        attempt: 1,
        allowed_decisions: ['respond'],
        items: [
          {
            resource_type: 'mcp_server',
            resource_name: 'Slack',
            tool_names: ['post_message'],
            credential_id: AUTH_CREDENTIAL_ID,
            fixable_by: 'me',
            connect: { kind: 'oauth' },
          },
        ],
      },
    }
    await page.route(`**/langgraph/threads/${conversationId}/state**`, async (route) => {
      const real = await route.fetch()
      const body = (await real.json()) as Record<string, unknown>
      const tasks = [{ id: 'run-auth', name: 'interrupted', interrupts: [interrupt] }]
      await route.fulfill({ response: real, json: { ...body, tasks, interrupts: [interrupt] } })
    })
    await t.visit(`/agents/${agentId}/conversations/${conversationId}`)
    await page.getByText('연결이 필요합니다').first().waitFor({ timeout: 60_000 })
    await settleUi(page)
    await t.screen('39-g05-auth-required-card.png')
  })
}
