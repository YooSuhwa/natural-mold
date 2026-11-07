import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Provider as JotaiProvider, createStore } from 'jotai'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { MemoryRecallChip } from '../memory-recall-chip'
import { ChatConversationContext } from '../conversation-context'
import { chatMemoryRecallAtom } from '@/lib/stores/chat-memory-recall'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

const memoriesQueryMock = vi.hoisted(() => ({ current: { data: undefined as unknown } }))

vi.mock('@/lib/hooks/use-memory', () => ({
  useMemories: () => memoriesQueryMock.current,
}))

function renderChip(store = createStore(), conversationId: string | null = 'conv-1') {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <JotaiProvider store={store}>
        <ChatConversationContext.Provider value={conversationId}>
          {children}
        </ChatConversationContext.Provider>
      </JotaiProvider>
    )
  }
  return render(<MemoryRecallChip />, { wrapper: Wrapper })
}

describe('MemoryRecallChip', () => {
  it('회상이 없으면 렌더하지 않는다', () => {
    const { container } = renderChip()
    expect(container.querySelector('[data-moldy-memory-recall]')).toBeNull()
  })

  it('다른 대화의 회상은 렌더하지 않는다 (대화 스코프)', () => {
    const store = createStore()
    store.set(chatMemoryRecallAtom, {
      'conv-OTHER': [{ id: 'm1', scope: 'user', content: '다른 대화 기억' }],
    })
    const { container } = renderChip(store)
    expect(container.querySelector('[data-moldy-memory-recall]')).toBeNull()
  })

  it('리로드(redacted) brief는 메모리 API 조인으로 내용을 복원한다', async () => {
    const user = userEvent.setup()
    memoriesQueryMock.current = {
      data: [{ id: 'm1', scope: 'user', content: '한국어로 답변 선호' }],
    }
    const store = createStore()
    store.set(chatMemoryRecallAtom, {
      'conv-1': [
        { id: 'm1', scope: 'user', content: '<redacted>' },
        { id: 'm-deleted', scope: 'agent', content: '<redacted>' },
      ],
    })
    renderChip(store)
    await user.click(screen.getByText('title'))
    // 조인 성공 → 원문 복원. 삭제된 기억은 hiddenContent 폴백.
    expect(screen.getByText('한국어로 답변 선호')).toBeInTheDocument()
    expect(screen.getByText('hiddenContent')).toBeInTheDocument()
    expect(screen.queryByText('<redacted>')).not.toBeInTheDocument()
  })

  it('회상 개수 메타를 보여주고 펼치면 scope 배지 + 내용을 보여준다', async () => {
    const user = userEvent.setup()
    const store = createStore()
    store.set(chatMemoryRecallAtom, {
      'conv-1': [
        { id: 'm1', scope: 'user', content: '한국어로 답변 선호' },
        { id: 'm2', scope: 'agent', content: '보고서는 표로 정리' },
      ],
    })
    renderChip(store)
    expect(screen.getByText('count(2)')).toBeInTheDocument()
    // 접힌 기본 상태.
    expect(screen.queryByText('한국어로 답변 선호')).not.toBeInTheDocument()
    await user.click(screen.getByText('title'))
    expect(screen.getByText('한국어로 답변 선호')).toBeInTheDocument()
    expect(screen.getByText('scopeUser')).toBeInTheDocument()
    expect(screen.getByText('scopeAgent')).toBeInTheDocument()
  })

  it('brief마다 경로(프로필/자동/도구)를 보이고, 경로 없는 옛 brief는 자동으로 본다', async () => {
    const user = userEvent.setup()
    memoriesQueryMock.current = { data: [] }
    const store = createStore()
    store.set(chatMemoryRecallAtom, {
      'conv-1': [
        // 사람이 직접 쓴 프로필 줄: 근거 기억이 없어 id가 없다.
        { lineId: 'line-1', scope: 'user', content: '이름은 지수', path: 'profile' },
        { id: 'm2', scope: 'agent', content: '보고서는 표로', path: 'auto' },
        { id: 'm3', scope: 'user', content: 'KTX 선호', path: 'tool' },
        // PR-B2 이전에 영속된 brief (path 없음).
        { id: 'm4', scope: 'user', content: '옛 회상' },
      ],
    })
    const { container } = renderChip(store)
    await user.click(screen.getByText('title'))
    expect(screen.getByText('이름은 지수')).toBeInTheDocument()
    expect(screen.getByText('옛 회상')).toBeInTheDocument()
    const paths = Array.from(
      container.querySelectorAll('[data-moldy-memory-recall-path]'),
      (node) => node.textContent,
    )
    expect(paths).toEqual(['path.profile', 'path.auto', 'path.tool', 'path.auto'])
  })

  it('리로드된 프로필 brief는 id가 있으면 조인하고 id가 없으면 비공개로 둔다', async () => {
    const user = userEvent.setup()
    memoriesQueryMock.current = {
      data: [{ id: 'm-src', scope: 'user', content: '회의는 오후' }],
    }
    const store = createStore()
    store.set(chatMemoryRecallAtom, {
      'conv-1': [
        { id: 'm-src', lineId: 'line-1', scope: 'user', content: '<redacted>', path: 'profile' },
        { lineId: 'line-2', scope: 'user', content: '<redacted>', path: 'profile' },
      ],
    })
    renderChip(store)
    await user.click(screen.getByText('title'))
    expect(screen.getByText('회의는 오후')).toBeInTheDocument()
    expect(screen.getByText('hiddenContent')).toBeInTheDocument()
  })
})
