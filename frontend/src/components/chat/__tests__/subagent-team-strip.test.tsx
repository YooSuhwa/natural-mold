import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Provider as JotaiProvider, createStore } from 'jotai'
import { describe, expect, it, vi } from 'vitest'
import type { SubagentDiscoverySnapshot } from '@langchain/react'
import type { ReactNode } from 'react'
import { SubagentTeamStrip } from '../subagent-team-strip'
import { ChatConversationContext } from '../conversation-context'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { chatSubagentNamesAtom } from '@/lib/stores/chat-subagent-names'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

const snapshotsMock = vi.hoisted(() => ({ current: [] as readonly SubagentDiscoverySnapshot[] }))

vi.mock('@/lib/chat/langgraph-runtime/subagent-runtime', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/lib/chat/langgraph-runtime/subagent-runtime')>()
  return {
    ...actual,
    useSubagentSnapshots: () => snapshotsMock.current,
  }
})

function snapshot(overrides: Partial<SubagentDiscoverySnapshot>): SubagentDiscoverySnapshot {
  return {
    id: 'call-1',
    name: 'agent_ab12cd34',
    namespace: ['task:1'],
    parentId: null,
    depth: 0,
    status: 'running',
    taskInput: '리서치 해줘',
    output: undefined,
    error: undefined,
    startedAt: new Date('2026-07-04T00:00:00Z'),
    completedAt: null,
    ...overrides,
  } as SubagentDiscoverySnapshot
}

function renderStrip(store = createStore(), conversationId: string | null = 'conv-1') {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <JotaiProvider store={store}>
        <ChatConversationContext.Provider value={conversationId}>
          {children}
        </ChatConversationContext.Provider>
      </JotaiProvider>
    )
  }
  return render(<SubagentTeamStrip />, { wrapper: Wrapper })
}

describe('SubagentTeamStrip', () => {
  it('스냅샷이 없으면 렌더하지 않는다', () => {
    snapshotsMock.current = []
    const { container } = renderStrip()
    expect(container.querySelector('[data-moldy-team-strip]')).toBeNull()
  })

  it('서브에이전트 칩을 상태 dot과 함께 렌더하고 진행 메타를 보여준다', () => {
    snapshotsMock.current = [
      snapshot({ id: 'c1', name: 'agent_a', status: 'running' }),
      snapshot({ id: 'c2', name: 'agent_b', status: 'complete', completedAt: new Date() }),
    ]
    renderStrip()
    expect(screen.getByText('agent_a')).toBeInTheDocument()
    expect(screen.getByText('agent_b')).toBeInTheDocument()
    // 진행 중이 있으면 runningMeta 우선.
    expect(screen.getByText('runningMeta(1)')).toBeInTheDocument()
    expect(document.querySelector('[data-moldy-team-chip="running"]')).not.toBeNull()
    expect(document.querySelector('[data-moldy-team-chip="complete"]')).not.toBeNull()
  })

  it('모두 종료되면 done/total 메타를 보여준다', () => {
    snapshotsMock.current = [
      snapshot({ id: 'c1', status: 'complete' }),
      snapshot({ id: 'c2', status: 'error', error: 'boom' }),
    ]
    renderStrip()
    expect(screen.getByText('doneMeta(2/2)')).toBeInTheDocument()
  })

  it('표시명 맵이 있으면 runtime_name을 display_name으로 치환한다', () => {
    snapshotsMock.current = [snapshot({ id: 'c1', name: 'agent_ab12cd34' })]
    const store = createStore()
    store.set(chatSubagentNamesAtom, { 'conv-1': { agent_ab12cd34: '리서처' } })
    renderStrip(store)
    expect(screen.getByText('리서처')).toBeInTheDocument()
    expect(screen.queryByText('agent_ab12cd34')).not.toBeInTheDocument()
  })

  it('칩 클릭 시 우측 레일 subagent 패널을 연다', async () => {
    const user = userEvent.setup()
    snapshotsMock.current = [snapshot({ id: 'call-9', name: 'agent_x', taskInput: '작업 입력' })]
    const store = createStore()
    renderStrip(store)
    await user.click(screen.getByText('agent_x'))
    expect(store.get(chatRightRailAtom)).toEqual({
      mode: 'subagent',
      subagent: {
        conversationId: 'conv-1',
        toolCallId: 'call-9',
        agentName: 'agent_x',
        input: '작업 입력',
      },
    })
  })

  it('중첩 서브에이전트(depth>0)는 ↳ 마커를 붙인다', () => {
    snapshotsMock.current = [
      snapshot({ id: 'c1', name: 'agent_parent' }),
      snapshot({ id: 'c2', name: 'agent_child', parentId: 'c1', depth: 1 }),
    ]
    renderStrip()
    expect(screen.getByText('↳')).toBeInTheDocument()
  })
})
