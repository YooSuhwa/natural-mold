/**
 * H-05 / plan 13.9 row 1: dashboard cards and the chat header list agents the server
 * already filtered by `can_run`; the settings entry points are hidden from runners.
 */
import { render, screen, userEvent } from '../../test-utils'
import { AgentCard } from '@/components/agent/agent-card'
import { ChatPageHeader } from '@/components/chat/chat-page-header'
import type { Agent } from '@/lib/types'

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/lib/hooks/use-agents', () => ({ useToggleFavorite: () => ({ mutate: vi.fn() }) }))
vi.mock('@/lib/hooks/use-readiness', () => ({ useReadinessSummary: () => ({ data: null }) }))
vi.mock('@/components/chat/side-chat/side-chat-panel', () => ({ SideChatToggle: () => null }))

const base: Agent = {
  id: 'agent-1',
  name: 'Test Agent',
  description: 'desc',
  system_prompt: '',
  model: { id: 'model-1', display_name: 'GPT-4o' },
  tools: [],
  skills: [],
  status: 'active',
  is_favorite: false,
  model_params: null,
  middleware_configs: [],
  template_id: null,
  created_at: '2026-01-15T00:00:00Z',
  updated_at: '2026-01-15T00:00:00Z',
} as unknown as Agent

const LEVELS: [string, string[] | undefined, boolean][] = [
  ['owner (legacy body)', undefined, true],
  [
    'owner',
    [
      'can_run',
      'can_view_config',
      'can_clone',
      'can_edit',
      'can_share',
      'can_deploy',
      'can_delete',
      'can_govern',
    ],
    true,
  ],
  ['editor', ['can_run', 'can_view_config', 'can_clone', 'can_edit'], true],
  ['viewer', ['can_run', 'can_view_config', 'can_clone'], true],
  ['runner', ['can_run'], false],
]

describe.each(LEVELS)('%s', (_label, permissions, settings) => {
  const agent = { ...base, my_permissions: permissions } as Agent

  it('agent card settings button', () => {
    render(<AgentCard agent={agent} />)
    expect(!!screen.queryByRole('button', { name: '설정' })).toBe(settings)
  })

  it('chat header menu settings item', async () => {
    render(
      <ChatPageHeader
        agent={agent}
        agentId="agent-1"
        title="t"
        onNewConversation={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenTrace={vi.fn()}
        onToggleArtifacts={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: '더보기 메뉴' }))
    expect(await screen.findByText('새 대화')).toBeInTheDocument()
    expect(!!screen.queryByText('설정')).toBe(settings)
  })
})
