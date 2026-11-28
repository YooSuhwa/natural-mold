// Feature: hitl-approval (features/hitl-approval.md), reject path.
// Precondition (API): install the seeded docx-document skill and attach it to a
// scripted-model agent. User path: send the HITL marker, see the approval card,
// 거부 -> 거부 확인, see 거부됨; the tool never ran (no artifact in the conversation).
export default async function ({ page, api, step, expect, scriptedModelId }) {
  const items = await api.get('/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200')
  const docx = items.find((i) => i.slug === 'docx-document')
  if (!docx) throw new Error('docx-document system skill is not seeded')
  const install = await api.post(`/api/marketplace/items/${docx.id}/install`, { install_mode: 'overwrite_existing' })
  const skillId = install.installed_skill_id ?? docx.installation?.installed_resource_id
  if (!skillId) throw new Error(`skill install returned no id: ${JSON.stringify(install).slice(0, 200)}`)

  const agent = await api.post('/api/agents', {
    name: `Verify HITL Agent ${Date.now()}`,
    system_prompt: 'Use execute_in_skill exactly when the scripted model requests it.',
    model_id: await scriptedModelId(),
    skill_ids: [skillId],
  })
  const conversation = await api.post(`/api/agents/${agent.id}/conversations`, { title: 'Verify HITL' })

  await page.goto(`/agents/${agent.id}/conversations/${conversation.id}`)
  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await expect(composer).toBeEnabled({ timeout: 60_000 })
  await composer.fill('mcp 도구 사용 승인 HITL')
  await composer.press('Enter')

  await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 30_000 })
  await step('approval-card')
  await page.getByRole('button', { name: '거부', exact: true }).last().click()
  await page.getByRole('button', { name: '거부 확인' }).last().click()
  await expect(page.getByText('거부됨').last()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('승인 응답을 전송하지 못했습니다. 다시 시도하세요.')).toHaveCount(0)
  await step('rejected')

  // Run must settle (idle) before the artifact list is meaningful.
  await expect
    .poll(async () => (await api.get(`/api/conversations/${conversation.id}/runs/active`))?.status ?? 'idle', { timeout: 30_000 })
    .toBe('idle')
  const artifacts = await api.get(`/api/conversations/${conversation.id}/artifacts`)
  const names = artifacts.map((a) => a.display_name)
  if (names.includes('moldy-docx-demo.docx')) throw new Error(`tool ran despite rejection: ${names.join(', ')}`)

  // Sidebar must stop flagging the conversation as "action required" once the
  // resumed run settles, without a reload (server list drops resumed parents).
  const attention = page.locator(`[data-moldy-run-attention="${conversation.id}"]`)
  const settleStart = Date.now()
  await expect(attention).toHaveCount(0, { timeout: 15_000 })
  const sidebarSettledMs = Date.now() - settleStart
  await step('sidebar-settled', `${sidebarSettledMs}ms after run idle`)
  return { agentId: agent.id, conversationId: conversation.id, artifacts: names, sidebarSettledMs }
}
