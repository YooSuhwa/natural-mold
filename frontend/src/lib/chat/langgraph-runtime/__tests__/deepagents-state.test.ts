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

  it('keeps stable file ids and deduplicates array and object files by path or id', () => {
    const state = selectDeepAgentsState({
      files: [
        { id: 'artifact-1', path: 'reports/brief.md', display_name: 'Brief' },
        { id: 'artifact-1', path: 'reports/brief-copy.md', display_name: 'Duplicate id' },
        { path: 'reports/brief.md', display_name: 'Duplicate path' },
        { path: 'notes/plain.txt', content: 'plain text' },
      ],
    })

    expect(state.files).toEqual([
      expect.objectContaining({
        id: 'artifact-1',
        name: 'Brief',
        path: 'reports/brief.md',
      }),
      expect.objectContaining({
        id: 'state-file:notes/plain.txt',
        name: 'plain.txt',
        path: 'notes/plain.txt',
      }),
    ])
  })

  it('normalizes artifact-shaped records into previewable deep agent files', () => {
    const state = selectDeepAgentsState({
      artifacts: [
        {
          id: 'artifact-md',
          path: 'reports/final.md',
          display_name: '최종 보고서',
          preview_url: '/api/artifacts/artifact-md/content',
          download_url: '/api/artifacts/artifact-md/download',
          artifact_kind: 'markdown',
          mime_type: 'text/markdown',
          size_bytes: 10,
        },
      ],
      files: {
        'reports/final.md': {
          content: '# Summary',
        },
      },
    })

    expect(state.files).toEqual([
      expect.objectContaining({
        id: 'artifact-md',
        name: '최종 보고서',
        path: 'reports/final.md',
        artifactKind: 'markdown',
        mimeType: 'text/markdown',
        previewUrl: '/api/artifacts/artifact-md/content',
        downloadUrl: '/api/artifacts/artifact-md/download',
        content: '# Summary',
      }),
    ])
  })
})
