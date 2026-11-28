// Feature: agent-create (features/agent-create.md)
// User path: /agents/new -> "직접 만들기" -> fill name -> 저장 -> lands on settings;
// API and the reloaded settings page both show the saved agent.
export default async function ({ page, api, step, expect }) {
  const name = `Verify Manual Agent ${Date.now()}`

  await page.goto('/agents/new')
  await expect(page.getByRole('heading', { name: '무엇을 만들고 싶으세요?' })).toBeVisible({ timeout: 60_000 })
  await step('creation-chooser')
  await page.getByRole('main').getByText('직접 만들기').click()
  await page.waitForURL(/\/agents\/new\/manual/, { timeout: 30_000 })

  await page.getByPlaceholder('에이전트 이름').fill(name)
  await step('form-filled')
  await page.getByRole('button', { name: '저장', exact: true }).click()
  await expect(page).toHaveURL(/\/agents\/[^/]+\/settings$/, { timeout: 30_000 })
  const agentId = page.url().match(/\/agents\/([^/]+)\/settings$/)[1]
  await step('saved-settings')

  const agent = await api.get(`/api/agents/${agentId}`)
  if (agent.name !== name) throw new Error(`API agent name ${agent.name} != ${name}`)

  await page.reload()
  await expect(page.getByPlaceholder('에이전트 이름')).toHaveValue(name, { timeout: 30_000 })
  await step('settings-after-reload')
  return { agentId, name, modelId: agent.model?.id ?? null, modelName: agent.model?.display_name ?? null }
}
