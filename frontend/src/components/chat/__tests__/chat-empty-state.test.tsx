import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatEmptyState } from '../chat-empty-state'
import type { Agent, Template } from '@/lib/types'

const setText = vi.fn()

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

vi.mock('@assistant-ui/react', () => ({
  useComposerRuntime: () => ({ setText }),
}))

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: ({ name }: { name: string }) => <span data-testid="avatar">{name}</span>,
}))

const useTemplatesMock = vi.fn()
vi.mock('@/lib/hooks/use-templates', () => ({
  useTemplates: (...args: unknown[]) => useTemplatesMock(...args),
}))

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'agent-1',
    name: '문서화 에이전트',
    description: null,
    system_prompt: 'p',
    status: 'active',
    is_favorite: false,
    image_url: null,
    opener_questions: null,
    template_id: null,
    tools: [],
    mcp_tools: [],
    skills: [],
    sub_agents: [],
    created_at: '2026-07-04T00:00:00Z',
    updated_at: '2026-07-04T00:00:00Z',
    ...overrides,
  } as unknown as Agent
}

const OPENWIKI_TEMPLATE = {
  id: 'tpl-1',
  usage_example: 'openwiki 저장소의 위키를 만들어줘',
} as unknown as Template

beforeEach(() => {
  setText.mockClear()
  useTemplatesMock.mockReset()
  useTemplatesMock.mockReturnValue({ data: undefined })
})

describe('ChatEmptyState', () => {
  it('스킬/도구/MCP 능력 칩을 상시 표시한다', () => {
    const agent = makeAgent({
      skills: [{ id: 's1', name: 'openwiki' }],
      tools: [{ id: 't1', name: 'Web Search' }],
      mcp_tools: [{ id: 'm1', name: 'notion_search', server_id: 'sv', server_name: 'Notion' }],
    } as Partial<Agent>)
    render(<ChatEmptyState agent={agent} fallback="대화를 시작해보세요" />)
    const chips = screen.getByText('openwiki').closest('[data-moldy-empty-capabilities]')
    expect(chips).not.toBeNull()
    expect(screen.getByText('emptyState.canDo')).toBeInTheDocument()
    expect(screen.getByText('Web Search')).toBeInTheDocument()
    expect(screen.getByText('notion_search')).toBeInTheDocument()
  })

  it('능력이 6개를 넘으면 +N 칩으로 접는다', () => {
    const agent = makeAgent({
      tools: Array.from({ length: 8 }, (_, i) => ({ id: `t${i}`, name: `도구 ${i}` })),
    } as Partial<Agent>)
    render(<ChatEmptyState agent={agent} fallback="시작" />)
    expect(screen.getByText('emptyState.moreCapabilities(2)')).toBeInTheDocument()
    expect(screen.queryByText('도구 7')).not.toBeInTheDocument()
  })

  it('opener가 있으면 템플릿 조회 없이 opener 스타터를 쓴다', () => {
    const agent = makeAgent({
      opener_questions: ['오늘 일정 알려줘'],
      template_id: 'tpl-1',
    } as Partial<Agent>)
    render(<ChatEmptyState agent={agent} fallback="시작" />)
    expect(screen.getByText('오늘 일정 알려줘')).toBeInTheDocument()
    expect(useTemplatesMock).toHaveBeenCalledWith(undefined, { enabled: false })
  })

  it('opener가 없으면 템플릿 usage_example을 스타터로 폴백하고 클릭 시 컴포저를 채운다', async () => {
    const user = userEvent.setup()
    useTemplatesMock.mockReturnValue({ data: [OPENWIKI_TEMPLATE] })
    const agent = makeAgent({ template_id: 'tpl-1' } as Partial<Agent>)
    render(<ChatEmptyState agent={agent} fallback="시작" />)
    const starter = screen.getByText('openwiki 저장소의 위키를 만들어줘')
    expect(useTemplatesMock).toHaveBeenCalledWith(undefined, { enabled: true })
    await user.click(starter)
    expect(setText).toHaveBeenCalledWith('openwiki 저장소의 위키를 만들어줘')
  })

  it('opener도 템플릿도 없으면 스타터 없이 렌더된다', () => {
    render(<ChatEmptyState agent={makeAgent()} fallback="시작" />)
    expect(document.querySelector('[data-moldy-empty-starters]')).toBeNull()
  })
})
