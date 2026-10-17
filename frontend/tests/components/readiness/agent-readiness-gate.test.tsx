import { act } from '@testing-library/react'

import { AgentReadinessGate } from '@/components/readiness/agent-readiness-gate'
import { announceAgentNotReady } from '@/lib/chat/agent-not-ready-signal'
import { render, screen } from '../../test-utils'

const readiness = vi.hoisted(() => ({ get: vi.fn(), notifyOwner: vi.fn(), summary: vi.fn() }))
vi.mock('@/lib/api/readiness', () => ({ readinessApi: readiness }))

const blocked = {
  overall: 'blocked',
  items: [{ kind: 'tool', count: 1, required: true, result: 'no_permission' }],
}

describe('AgentReadinessGate (S21 on the chat screen)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows nothing for a ready agent', async () => {
    readiness.get.mockResolvedValue({ overall: 'ready', items: [] })
    render(<AgentReadinessGate agentId="agent-1" />)
    await act(async () => {})
    expect(screen.queryByRole('button', { name: '점검 보기' })).not.toBeInTheDocument()
  })

  it('folds a not-ready agent into a card and opens the panel on 409 agent_not_ready', async () => {
    readiness.get.mockResolvedValue(blocked)
    render(<AgentReadinessGate agentId="agent-1" agentName="영업 리포트" />)
    expect(await screen.findByText('지금은 이 에이전트를 쓸 수 없어요')).toBeInTheDocument()
    act(() => announceAgentNotReady({ details: null, agentId: 'side-chat-agent' }))
    expect(screen.queryByText('영업 리포트 시작 전 점검')).not.toBeInTheDocument()
    act(() => announceAgentNotReady({ details: null, agentId: 'agent-1' }))
    expect(await screen.findByText('영업 리포트 시작 전 점검')).toBeInTheDocument()
    expect(
      await screen.findByText('필수 항목을 쓸 수 없어 대화를 시작할 수 없어요'),
    ).toBeInTheDocument()
  })
})
