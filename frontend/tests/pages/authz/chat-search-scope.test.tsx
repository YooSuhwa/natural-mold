import { renderHook } from '@testing-library/react'

import { AgentContextPopover } from '@/components/agent/agent-context-popover'
import { useResourceContextCandidates } from '@/lib/chat/context/use-resource-context-candidates'
import type { Agent } from '@/lib/types'
import { render, screen, userEvent } from '../../test-utils'

// Plan 13.9 row 13: pickers show only what the org/permission-filtered server APIs
// returned. Clearing every cache on an organization switch is locked by
// tests/components/layout/org-switch-reset.test.tsx (zero previous-org ids in the cache).
const server = vi.hoisted(() => ({ files: vi.fn(), artifacts: vi.fn(), conversations: vi.fn() }))

vi.mock('@/lib/hooks/use-conversation-files', () => ({
  useConversationFiles: () => server.files(),
}))
vi.mock('@/lib/hooks/use-artifact-library', () => ({
  useArtifactLibrary: () => server.artifacts(),
}))
vi.mock('@/lib/hooks/use-conversations', () => ({
  useGlobalConversationPages: () => server.conversations(),
}))

const page = <T,>(items: T[]) => ({
  data: { pages: [{ items }] },
  isLoading: false,
  isError: false,
})

describe('resource context picker scope', () => {
  it('lists exactly the rows the server search answered', () => {
    server.files.mockReturnValue({
      data: [{ source: 'attached', id: 'f-1', name: 'a.pdf', mime_type: 'application/pdf' }],
      isLoading: false,
      isError: false,
    })
    server.artifacts.mockReturnValue(
      page([
        {
          status: 'ready',
          id: 'art-1',
          version_id: 'v1',
          display_name: '보고서',
          mime_type: 'text/md',
        },
      ]),
    )
    server.conversations.mockReturnValue(page([{ id: 'c-1', title: '내 대화' }]))
    const { result } = renderHook(() =>
      useResourceContextCandidates({ conversationId: 'c-0', linkedSkills: [] }),
    )
    expect(result.current.candidates.map((c) => `${c.kind}:${c.id}`)).toEqual([
      'file:f-1',
      'artifact:art-1',
      'conversation:c-1',
    ])
  })

  it('shows nothing extra when the server answers empty (e.g. right after an org switch)', () => {
    server.files.mockReturnValue({ data: [], isLoading: false, isError: false })
    server.artifacts.mockReturnValue(page([]))
    server.conversations.mockReturnValue(page([]))
    const { result } = renderHook(() => useResourceContextCandidates({ conversationId: 'c-0' }))
    expect(result.current.candidates).toEqual([])
  })
})

function agent(myPermissions?: string[]): Agent {
  return {
    id: 'a-1',
    name: '리서치',
    description: null,
    my_permissions: myPermissions,
  } as Agent
}

async function openPopover(subject: Agent) {
  render(<AgentContextPopover agent={subject} agentId="a-1" />)
  await userEvent.click(screen.getByRole('button', { name: '에이전트 정보' }))
}

describe('agent context popover', () => {
  it.each([
    ['owner (legacy body)', undefined, true],
    ['editor', ['can_run', 'can_view_config', 'can_edit'], true],
    ['viewer', ['can_run', 'can_view_config'], true],
    ['runner', ['can_run'], false],
  ])('%s: settings entry shown = %s', async (_level, permissions, shown) => {
    await openPopover(agent(permissions))
    expect(await screen.findByText('새 채팅')).toBeInTheDocument()
    expect(screen.queryByText('설정') !== null).toBe(shown)
  })
})
