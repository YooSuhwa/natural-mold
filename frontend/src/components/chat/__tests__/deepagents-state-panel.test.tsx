import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { DeepAgentsStatePanel } from '../deepagents-state-panel'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'

const state: DeepAgentsStateSnapshot = {
  todos: [
    { id: 'todo-1', content: 'Plan work', status: 'completed' },
    { id: 'todo-2', content: 'Write draft', status: 'in_progress' },
    { id: 'todo-3', content: 'Review result', status: 'pending' },
  ],
  files: [{ id: 'file-1', name: 'brief.md', path: 'reports/brief.md', sizeBytes: 1200 }],
}

describe('DeepAgentsStatePanel', () => {
  it('renders grouped task state and file count', () => {
    render(<DeepAgentsStatePanel state={state} />)

    expect(screen.getByText('작업 목록')).toBeInTheDocument()
    expect(screen.getByText('1/3 완료')).toBeInTheDocument()
    expect(screen.getByText('Plan work')).toBeInTheDocument()
    expect(screen.getByText('Write draft')).toBeInTheDocument()
    expect(screen.getByText('Review result')).toBeInTheDocument()
    expect(screen.getByText('파일')).toBeInTheDocument()
    expect(screen.getByText('1개')).toBeInTheDocument()
    expect(screen.getByText('brief.md')).toBeInTheDocument()
  })

  it('renders nothing without todos or files', () => {
    const { container } = render(<DeepAgentsStatePanel state={{ todos: [], files: [] }} />)

    expect(container).toBeEmptyDOMElement()
  })
})
