import { test, expect } from './fixtures'
import type { Page, Route } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

const now = '2026-06-10T12:00:00Z'
const captureDir = join(process.cwd(), '..', 'output', 'e2e-captures', '20260611-chat-navigator')

const agents = [
  {
    id: 'agent-1',
    name: 'Alpha Agent',
    description: 'Research and planning assistant',
    status: 'active',
    is_favorite: false,
    image_url: null,
    model_display_name: 'GPT-4o',
    tool_count: 2,
    fallback_count: 0,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    last_used_at: '2026-06-10T12:00:00Z',
    unread_count: 0,
  },
  {
    id: 'agent-2',
    name: 'Beta Agent',
    description: 'Writing assistant',
    status: 'active',
    is_favorite: false,
    image_url: null,
    model_display_name: 'GPT-4o',
    tool_count: 1,
    fallback_count: 0,
    created_at: '2026-06-02T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
    last_used_at: '2026-06-09T12:00:00Z',
    unread_count: 3,
  },
]

const agentDetails = agents.map((agent) => ({
  ...agent,
  runtime_name: `${agent.name.toLowerCase().replaceAll(' ', '-')}`,
  identity_mode: 'fixed',
  system_prompt: 'You are helpful.',
  model: { id: 'model-1', display_name: 'GPT-4o' },
  tools: [{ id: 'tool-1', name: 'Web Search' }],
  mcp_tools: [],
  skills: [],
  sub_agents: [],
  model_params: null,
  middleware_configs: [],
  template_id: null,
  opener_questions: null,
  llm_credential_id: null,
  model_fallback_ids: [],
}))

const conversations = [
  {
    id: 'conv-1',
    agent_id: 'agent-1',
    title: 'Alpha kickoff',
    is_pinned: false,
    unread_count: 0,
    last_read_at: null,
    last_unread_at: null,
    last_activity_source: 'user',
    created_at: '2026-06-08T10:00:00Z',
    updated_at: now,
    active_run: null,
  },
  {
    id: 'conv-2',
    agent_id: 'agent-1',
    title: 'Second hidden session',
    is_pinned: true,
    unread_count: 0,
    last_read_at: null,
    last_unread_at: null,
    last_activity_source: 'user',
    created_at: '2026-06-07T10:00:00Z',
    updated_at: '2026-06-09T10:00:00Z',
    active_run: null,
  },
  {
    id: 'conv-3',
    agent_id: 'agent-2',
    title: 'Beta recent session',
    is_pinned: false,
    unread_count: 2,
    last_read_at: null,
    last_unread_at: '2026-06-09T12:00:00Z',
    last_activity_source: 'schedule',
    created_at: '2026-06-06T10:00:00Z',
    updated_at: '2026-06-09T12:00:00Z',
    active_run: null,
  },
]

function withAgent(conversation: (typeof conversations)[number]) {
  const agent = agents.find((item) => item.id === conversation.agent_id) ?? agents[0]
  return {
    ...conversation,
    agent: {
      id: agent.id,
      name: agent.name,
      image_url: agent.image_url,
    },
  }
}

function filteredConversations(url: URL, agentId?: string) {
  const query = (url.searchParams.get('q') ?? '').toLowerCase()
  return conversations.filter((conversation) => {
    if (agentId && conversation.agent_id !== agentId) return false
    return query ? (conversation.title ?? '').toLowerCase().includes(query) : true
  })
}

async function mockApi(route: Route) {
  const url = new URL(route.request().url())
  const pathname = url.pathname
  if (pathname === '/api/auth/me') {
    await route.fulfill({
      json: {
        id: 'user-1',
        email: 'e2e@moldy.dev',
        name: 'E2E User',
        is_super_user: true,
      },
    })
    return
  }
  if (pathname === '/api/agents/summary') {
    await route.fulfill({ json: agents })
    return
  }
  if (pathname.startsWith('/api/agents/') && pathname.endsWith('/conversations/page')) {
    const agentId = pathname.split('/')[3]
    await route.fulfill({
      json: {
        items: filteredConversations(url, agentId),
        next_cursor: null,
        has_more: false,
      },
    })
    return
  }
  if (pathname === '/api/conversations/page') {
    await route.fulfill({
      json: {
        items: filteredConversations(url).map(withAgent),
        next_cursor: null,
        has_more: false,
      },
    })
    return
  }
  if (/^\/api\/agents\/[^/]+$/.test(pathname)) {
    const agentId = pathname.split('/')[3]
    await route.fulfill({
      json: agentDetails.find((agent) => agent.id === agentId) ?? agentDetails[0],
    })
    return
  }
  if (/^\/api\/conversations\/[^/]+$/.test(pathname)) {
    const conversationId = pathname.split('/')[3]
    const conversation = conversations.find((item) => item.id === conversationId)
    await route.fulfill({
      json: conversation ? withAgent(conversation) : withAgent(conversations[0]),
    })
    return
  }
  if (pathname.endsWith('/messages')) {
    await route.fulfill({
      json: {
        messages: [],
        active_tip_message_id: null,
        active_checkpoint_id: null,
        total_estimated_cost: 0,
      },
    })
    return
  }
  await route.fulfill({ json: { items: [], total: 0 } })
}

async function setupNavigatorPage(page: Page) {
  await page.route('**/api/**', mockApi)
  await page.goto('/agents/agent-1/conversations/conv-1')
  await page.waitForLoadState('domcontentloaded')
}

async function capturePage(page: Page, name: string) {
  mkdirSync(captureDir, { recursive: true })
  await page.screenshot({ path: join(captureDir, name), fullPage: true })
}

test.describe('Chat navigator consolidation', () => {
  test('shows one consolidated navigator with search, menus, and shortcuts', async ({
    page,
    errors,
  }) => {
    await setupNavigatorPage(page)

    await expect(page.getByText('에이전트').first()).toBeVisible()
    await expect(page.getByText('Alpha Agent').first()).toBeVisible()
    await expect(page.getByText('Alpha kickoff').first()).toBeVisible()
    await expect(page.getByRole('textbox', { name: '에이전트 또는 대화 검색' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Alpha Agent 대화 검색' })).toHaveCount(0)
    await capturePage(page, 'chat-navigator-default.png')

    const kickoffRow = page.locator(
      '[data-chat-session-href="/agents/agent-1/conversations/conv-1"]',
    )
    await kickoffRow.hover()
    await kickoffRow.getByRole('button', { name: '대화 메뉴' }).click()
    await expect(page.getByRole('menuitem', { name: /이름 변경/ })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: /공유/ })).toBeVisible()
    await capturePage(page, 'chat-navigator-row-menu.png')
    await page.keyboard.press('Escape')

    const modifier = process.platform === 'darwin' ? 'Meta' : 'Control'
    await page.keyboard.down(modifier)
    await expect(
      page.getByText(process.platform === 'darwin' ? '⌘⇧1' : 'Ctrl+Shift+1'),
    ).toBeVisible()
    await capturePage(page, 'chat-navigator-shortcuts.png')
    await page.keyboard.up(modifier)

    await page.keyboard.press(`${modifier}+K`)
    await expect(page.getByRole('heading', { name: '빠른 이동' })).toBeVisible()
    await capturePage(page, 'chat-navigator-quick-switcher.png')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('heading', { name: '빠른 이동' })).not.toBeVisible()

    await page.getByRole('button', { name: '에이전트 검색' }).click()
    await page.getByRole('textbox', { name: '에이전트 또는 대화 검색' }).fill('Second')
    await expect(page.getByText('검색 결과')).toBeVisible()
    await expect(page.getByText('Second hidden session').first()).toBeVisible()
    await expect(page.getByText('검색 결과가 없습니다')).toHaveCount(0)
    await capturePage(page, 'chat-navigator-search.png')

    await page.getByRole('button', { name: '새 채팅' }).click()
    await expect(page).toHaveURL(/\/agents\/agent-1\/conversations\/new$/)

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })

  test('drives view mode and agent sort from the navigator options menu', async ({
    page,
    errors,
  }) => {
    await setupNavigatorPage(page)

    // 에이전트 그룹 헤더 링크의 DOM 순서 (정렬 단언용)
    // 메인 채팅 헤더 breadcrumb에도 /agents/<id> 링크가 있으므로 사이드바로 스코프를 좁힌다
    const sidebar = page.locator('[data-sidebar="sidebar"]')
    const agentHeaderOrder = () =>
      sidebar
        .locator('a[href="/agents/agent-1"], a[href="/agents/agent-2"]')
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')))

    // 기본 상태: 에이전트별 보기 + 최근 사용 정렬(Alpha가 위), 비활성 에이전트 세션은 접혀 있다
    await expect(page.getByText('Alpha kickoff').first()).toBeVisible()
    await expect(page.getByText('Beta recent session')).toHaveCount(0)
    await expect
      .poll(agentHeaderOrder)
      .toEqual(['/agents/agent-1', '/agents/agent-2'])

    // 트리거는 opacity-0이라 헤딩 hover로 노출시킨 뒤 연다 (Playwright는 opacity-0도 클릭 가능하지만 캡처를 위해)
    // Base UI 라디오 항목은 closeOnClick=false라 메뉴가 유지되므로, 한 번 열어 연속으로 조작한다
    const menuTrigger = page.getByRole('button', { name: '탐색 옵션' })
    await menuTrigger.hover()
    await menuTrigger.click()
    await expect(page.getByRole('menuitem', { name: '보기 방식' })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '에이전트 정렬' })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '대화 정렬' })).toBeVisible()
    await expect(
      page.getByRole('menuitemcheckbox', { name: '한 번에 하나만 펼치기' }),
    ).toBeVisible()
    await capturePage(page, 'chat-navigator-options-menu.png')

    // 에이전트 정렬을 생성순으로 바꾸면 Beta(06-02 생성)가 Alpha(06-01 생성) 위로 올라온다
    await page.getByRole('menuitem', { name: '에이전트 정렬' }).hover()
    await expect(page.getByRole('menuitemradio', { name: '최근 사용' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await page.getByRole('menuitemradio', { name: '생성순' }).click()
    await expect
      .poll(agentHeaderOrder)
      .toEqual(['/agents/agent-2', '/agents/agent-1'])
    await capturePage(page, 'chat-navigator-agent-sort-created.png')

    // 보기 방식 서브메뉴: 세 가지 모드 라디오와 현재 선택(에이전트별)을 확인한다
    await page.getByRole('menuitem', { name: '보기 방식' }).hover()
    await expect(page.getByRole('menuitemradio', { name: '에이전트별' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await expect(page.getByRole('menuitemradio', { name: '최근 에이전트' })).toBeVisible()
    await expect(page.getByRole('menuitemradio', { name: '최근 대화' })).toBeVisible()
    await capturePage(page, 'chat-navigator-view-modes.png')

    // 최근 대화 모드로 전환: 그룹 헤더가 사라지고 모든 세션이 에이전트 아바타와 함께 평탄화된다
    await page.getByRole('menuitemradio', { name: '최근 대화' }).click()
    // Escape는 한 레벨씩 닫는다 (서브메뉴 → 루트 메뉴)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menuitem', { name: '보기 방식' })).toHaveCount(0)
    const betaRow = page.locator(
      '[data-chat-session-href="/agents/agent-2/conversations/conv-3"]',
    )
    await expect(betaRow).toBeVisible()
    // 에이전트 이름은 아바타 hover 툴팁으로 노출된다
    await betaRow.locator('[data-slot="tooltip-trigger"]').hover()
    await expect(
      page.locator('[data-slot="tooltip-content"]').getByText('Beta Agent'),
    ).toBeVisible()
    await expect(page.getByText('Alpha kickoff').first()).toBeVisible()
    await expect(sidebar.locator('a[href="/agents/agent-1"]')).toHaveCount(0)
    await capturePage(page, 'chat-navigator-recent-sessions.png')

    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  })
})
