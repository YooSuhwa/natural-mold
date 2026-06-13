import { describe, expect, it } from 'vitest'
import { selectDeepAgentsState } from '../deepagents-state'

describe('selectDeepAgentsState', () => {
  it('normalizes todo statuses and labels from loose state values', () => {
    const state = selectDeepAgentsState({
      todos: [
        { id: 'todo-1', content: 'Plan work', status: 'completed' },
        { id: 'todo-2', task: 'Write draft', status: 'in_progress' },
        { id: 'todo-3', title: 'Review result', status: 'todo' },
      ],
    })

    expect(state.todos).toEqual([
      expect.objectContaining({ id: 'todo-1', content: 'Plan work', status: 'completed' }),
      expect.objectContaining({ id: 'todo-2', content: 'Write draft', status: 'in_progress' }),
      expect.objectContaining({ id: 'todo-3', content: 'Review result', status: 'pending' }),
    ])
  })

  it('normalizes files from arrays and record maps', () => {
    const state = selectDeepAgentsState({
      files: {
        'reports/brief.md': { size_bytes: 1200, mime_type: 'text/markdown' },
        'notes.txt': 'hello',
      },
    })

    expect(state.files).toEqual([
      expect.objectContaining({
        id: 'state-file:reports/brief.md',
        name: 'brief.md',
        path: 'reports/brief.md',
        sizeBytes: 1200,
      }),
      expect.objectContaining({
        id: 'state-file:notes.txt',
        name: 'notes.txt',
        path: 'notes.txt',
      }),
    ])
  })
})
