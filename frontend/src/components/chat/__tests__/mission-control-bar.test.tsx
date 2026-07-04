import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MissionControlBar } from '../mission-control-bar'
import type { DeepAgentTodo } from '@/lib/chat/langgraph-runtime/deepagents-state'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

const TODOS: readonly DeepAgentTodo[] = [
  { id: 't1', content: '저장소 구조 조사', status: 'completed' },
  { id: 't2', content: 'quickstart.md 작성', status: 'in_progress' },
  { id: 't3', content: '위키 게시', status: 'pending' },
]

describe('MissionControlBar', () => {
  it('todos가 없으면 렌더하지 않는다', () => {
    const { container } = render(<MissionControlBar todos={[]} />)
    expect(container.querySelector('[data-moldy-mission-control]')).toBeNull()
  })

  it('접힌 상태에서 진행 요약(done/total)을 보여준다', () => {
    render(<MissionControlBar todos={TODOS} />)
    expect(screen.getByText('tasks.progress(1/3)')).toBeInTheDocument()
    // 접힌 기본 상태 — 개별 todo 행은 보이지 않는다.
    expect(screen.queryByText('quickstart.md 작성')).not.toBeInTheDocument()
  })

  it('펼치면 상태 그룹 순서(in_progress→pending→completed)로 todo 행을 보여준다', async () => {
    const user = userEvent.setup()
    render(<MissionControlBar todos={TODOS} />)
    await user.click(screen.getByText('tasks.title'))
    const items = screen.getAllByRole('listitem').map((li) => li.textContent ?? '')
    expect(items[0]).toContain('quickstart.md 작성')
    expect(items[1]).toContain('위키 게시')
    expect(items[2]).toContain('저장소 구조 조사')
  })

  it('모든 todo 완료 시에도 요약을 유지한다', () => {
    render(
      <MissionControlBar
        todos={TODOS.map((todo) => ({ ...todo, status: 'completed' as const }))}
      />,
    )
    expect(screen.getByText('tasks.progress(3/3)')).toBeInTheDocument()
  })
})
