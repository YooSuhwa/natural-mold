// Feature: chat-send (features/chat-send.md)
// Precondition (API): an agent on the keyless scripted model + an empty conversation.
// User path (UI): open the conversation, type in the composer, press Enter, see the
// streamed assistant reply; reload and see both bubbles again; API confirms persistence.
const REPLY = 'E2E scripted document model is ready.'

export default async function ({ page, api, step, expect, scriptedModelId }) {
  const text = `verify chat ${Date.now()}`
  const agent = await api.post('/api/agents', {
    name: `Verify Chat Agent ${Date.now()}`,
    system_prompt: 'Reply briefly.',
    model_id: await scriptedModelId(),
  })
  const conversation = await api.post(`/api/agents/${agent.id}/conversations`, { title: 'Verify chat' })

  await page.goto(`/agents/${agent.id}/conversations/${conversation.id}`)
  await expect(page.getByRole('main').getByRole('heading', { name: agent.name }).first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('대화를 시작해보세요.')).toBeVisible({ timeout: 20_000 })
  await step('empty-conversation')

  const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
  await expect(composer).toBeEnabled({ timeout: 20_000 })
  await composer.fill(text)
  await step('typed-message')
  await composer.press('Enter')

  const userBubble = page.locator('[data-moldy-message-role="user"]').filter({ hasText: text })
  const assistantBubble = page.locator('[data-moldy-message-role="assistant"]').filter({ hasText: REPLY })
  await expect(userBubble).toHaveCount(1, { timeout: 20_000 })
  await expect(assistantBubble).toHaveCount(1, { timeout: 60_000 })
  await expect(composer).toHaveValue('')
  await step('reply-streamed')

  await page.reload()
  await expect(page.locator('[data-moldy-message-role="user"]').filter({ hasText: text })).toHaveCount(1, { timeout: 30_000 })
  await expect(page.locator('[data-moldy-message-role="assistant"]').filter({ hasText: REPLY })).toHaveCount(1, { timeout: 30_000 })
  await step('after-reload')

  const stored = JSON.stringify(await api.get(`/api/conversations/${conversation.id}/messages`))
  if (!stored.includes(text) || !stored.includes(REPLY)) {
    throw new Error(`messages API does not contain both turns: ${stored.slice(0, 500)}`)
  }
  return { agentId: agent.id, conversationId: conversation.id, sent: text, reply: REPLY }
}
