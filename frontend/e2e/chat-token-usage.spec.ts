import { test, expect } from './fixtures'

const ISO = '2026-06-04T00:00:00.000Z'
const AGENT_ID = 'agent-token-usage'
const CONVERSATION_ID = 'conversation-token-usage'

const FAKE_AGENT = {
  id: AGENT_ID,
  name: 'Token Inspector',
  description: 'E2E fixture agent for chat token usage hover.',
  system_prompt: 'You expose token usage metadata for testing.',
  model: { id: 'model-gpt-5-mini', display_name: 'GPT-5 mini' },
  tools: [],
  mcp_tools: [],
  skills: [],
  sub_agents: [],
  status: 'active',
  is_favorite: false,
  model_params: { temperature: 0.7, top_p: 1, max_tokens: 4096 },
  middleware_configs: [],
  template_id: null,
  created_at: ISO,
  updated_at: ISO,
  last_used_at: ISO,
  image_url: null,
  opener_questions: [],
  llm_credential_id: null,
  unread_count: 0,
  model_fallback_ids: null,
}

const FAKE_CONVERSATION = {
  id: CONVERSATION_ID,
  agent_id: AGENT_ID,
  title: 'Token usage hover',
  is_pinned: false,
  unread_count: 0,
  last_read_at: ISO,
  last_unread_at: null,
  last_activity_source: 'assistant',
  created_at: ISO,
  updated_at: ISO,
}

const FAKE_MESSAGES = {
  messages: [
    {
      id: 'message-user-token-usage',
      conversation_id: CONVERSATION_ID,
      role: 'user',
      content: '토큰 사용량 hover를 확인해줘.',
      tool_calls: null,
      tool_call_id: null,
      created_at: ISO,
      feedback: null,
      attachments: null,
      parent_id: null,
      branch_checkpoint_id: null,
      siblings: [],
      sibling_checkpoint_ids: [],
      branch_index: null,
      branch_total: null,
      usage: null,
    },
    {
      id: 'message-assistant-token-usage',
      conversation_id: CONVERSATION_ID,
      role: 'assistant',
      content: '토큰 사용량 상세 팝오버가 정상적으로 표시되는 응답입니다.',
      tool_calls: null,
      tool_call_id: null,
      created_at: ISO,
      feedback: null,
      attachments: null,
      parent_id: 'message-user-token-usage',
      branch_checkpoint_id: null,
      siblings: [],
      sibling_checkpoint_ids: [],
      branch_index: null,
      branch_total: null,
      usage: {
        prompt_tokens: 1200,
        completion_tokens: 900,
        cache_creation_tokens: 400,
        cache_read_tokens: 250,
        estimated_cost: 0.0123,
      },
    },
  ],
  active_tip_message_id: 'message-assistant-token-usage',
  active_checkpoint_id: 'checkpoint-token-usage',
  total_estimated_cost: 0.0123,
}

test.describe('Chat token usage hover', () => {
  test('shows the assistant message token breakdown and cost on hover', async ({ page, errors }) => {
    await page.route('**/api/agents/summary', (route) =>
      route.fulfill({
        json: [
          {
            id: AGENT_ID,
            name: FAKE_AGENT.name,
            description: FAKE_AGENT.description,
            status: FAKE_AGENT.status,
            is_favorite: FAKE_AGENT.is_favorite,
            image_url: FAKE_AGENT.image_url,
            model_display_name: FAKE_AGENT.model.display_name,
            tool_count: 0,
            fallback_count: 0,
            created_at: FAKE_AGENT.created_at,
            updated_at: FAKE_AGENT.updated_at,
            last_used_at: FAKE_AGENT.last_used_at,
            unread_count: FAKE_AGENT.unread_count,
          },
        ],
      }),
    )
    await page.route('**/api/triggers/summary', (route) =>
      route.fulfill({ json: { total_unread: 0 } }),
    )
    await page.route(`**/api/agents/${AGENT_ID}`, (route) => route.fulfill({ json: FAKE_AGENT }))
    await page.route(`**/api/agents/${AGENT_ID}/conversations/page**`, (route) =>
      route.fulfill({
        json: { items: [FAKE_CONVERSATION], next_cursor: null, has_more: false },
      }),
    )
    await page.route(`**/api/conversations/${CONVERSATION_ID}`, (route) =>
      route.fulfill({ json: FAKE_CONVERSATION }),
    )
    await page.route(`**/api/conversations/${CONVERSATION_ID}/messages`, (route) =>
      route.fulfill({ json: FAKE_MESSAGES }),
    )

    await page.goto(`/agents/${AGENT_ID}/conversations/${CONVERSATION_ID}`)
    await page.waitForLoadState('domcontentloaded')

    await expect(
      page.getByText('토큰 사용량 상세 팝오버가 정상적으로 표시되는 응답입니다.'),
    ).toBeVisible()

    const tokenButton = page.getByRole('button', { name: '토큰 사용량 보기' })
    await expect(tokenButton).toBeVisible()
    await expect(tokenButton).toContainText('2,100')

    await tokenButton.hover()

    const tooltip = page.getByRole('tooltip').filter({ hasText: '토큰 사용량' })
    await expect(tooltip).toBeVisible()
    await expect(tooltip).toContainText('2,100 합계')
    await expect(tooltip).toContainText('입력')
    await expect(tooltip).toContainText('1,200')
    await expect(tooltip).toContainText('출력')
    await expect(tooltip).toContainText('900')
    await expect(tooltip).toContainText('캐시 생성')
    await expect(tooltip).toContainText('400')
    await expect(tooltip).toContainText('캐시 적중')
    await expect(tooltip).toContainText('250')
    await expect(tooltip).toContainText('추정 비용')
    await expect(tooltip).toContainText('$0.0123')

    await expect(page.locator('[data-base-ui-portal]').filter({ has: tooltip })).toHaveCount(1)
    await expect(
      tooltip.evaluate((node) => Boolean(node.closest('section.moldy-panel'))),
      'tooltip should render outside the overflow-hidden chat panel',
    ).resolves.toBe(false)

    const box = await tooltip.boundingBox()
    expect(box, 'tooltip should have a browser layout box').not.toBeNull()
    expect(box?.width ?? 0).toBeGreaterThan(100)
    expect(box?.height ?? 0).toBeGreaterThan(80)

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
})
