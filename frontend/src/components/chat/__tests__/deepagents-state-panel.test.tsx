import { describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../tests/test-utils'
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
  it('characterizes grouped task state and collapsed file count rendering', () => {
    render(<DeepAgentsStatePanel state={state} />)

    expect(screen.getByText('작업 목록')).toBeInTheDocument()
    expect(screen.getByText('1/3 완료')).toBeInTheDocument()
    expect(screen.getByText('Plan work')).toBeInTheDocument()
    expect(screen.getByText('Write draft')).toBeInTheDocument()
    expect(screen.getByText('Review result')).toBeInTheDocument()
    expect(screen.getByText('파일')).toBeInTheDocument()
    expect(screen.getByText('1개')).toBeInTheDocument()
    expect(screen.queryByText('brief.md')).not.toBeInTheDocument()
  })

  it('shows file preview affordance labels and action buttons', async () => {
    const previewState: DeepAgentsStateSnapshot = {
      todos: [],
      files: [
        {
          id: 'artifact-md',
          name: '최종 보고서',
          path: 'reports/final.md',
          mimeType: 'text/markdown',
          artifactKind: 'markdown',
          previewUrl: '/api/artifacts/artifact-md/content',
          downloadUrl: '/api/artifacts/artifact-md/download',
          content: '# Summary',
        },
        {
          id: 'code-1',
          name: 'analysis.py',
          path: 'src/analysis.py',
          mimeType: 'text/x-python',
          artifactKind: 'code',
          content: 'print("ok")',
        },
        {
          id: 'plain-1',
          name: 'notes.txt',
          path: 'notes.txt',
          mimeType: 'text/plain',
          content: 'plain notes',
        },
      ],
    }

    const user = userEvent.setup()
    const onOpenPreview = vi.fn()
    const onCopyFile = vi.fn()
    const onDownloadFile = vi.fn()

    render(
      <DeepAgentsStatePanel
        state={previewState}
        onOpenPreview={onOpenPreview}
        onCopyFile={onCopyFile}
        onDownloadFile={onDownloadFile}
      />,
    )

    await screen.findByText('최종 보고서')
    expect(screen.getByText('마크다운')).toBeInTheDocument()
    expect(screen.getByText('코드')).toBeInTheDocument()
    expect(screen.getByText('텍스트')).toBeInTheDocument()
    const previewButton = screen.getByRole('button', { name: '최종 보고서 미리보기 열기' })
    const copyButton = screen.getByRole('button', { name: '최종 보고서 복사' })
    const downloadLink = screen.getByRole('link', { name: '최종 보고서 다운로드' })
    expect(previewButton).toBeEnabled()
    expect(copyButton).toBeEnabled()
    expect(downloadLink).toHaveAttribute('href', '/api/artifacts/artifact-md/download')
    expect(screen.getByRole('button', { name: 'analysis.py 복사' })).toBeEnabled()
    downloadLink.addEventListener('click', (event) => event.preventDefault())
    await user.click(previewButton)
    await user.click(copyButton)
    await user.click(downloadLink)
    expect(onOpenPreview).toHaveBeenCalledWith(previewState.files[0])
    expect(onCopyFile).toHaveBeenCalledWith(previewState.files[0])
    expect(onDownloadFile).toHaveBeenCalledWith(previewState.files[0])
    expect(screen.queryByText('assistant transcript')).not.toBeInTheDocument()
  })

  it('renders nothing without todos or files', () => {
    const { container } = render(<DeepAgentsStatePanel state={{ todos: [], files: [] }} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('disables edit and save actions while loading or interrupted', async () => {
    const stateWithEditableFile: DeepAgentsStateSnapshot = {
      todos: [],
      files: [
        {
          id: 'artifact-md',
          name: '최종 보고서',
          path: 'reports/final.md',
          mimeType: 'text/markdown',
          artifactKind: 'markdown',
          content: '# Summary',
        },
      ],
    }

    render(<DeepAgentsStatePanel state={stateWithEditableFile} isLoading isInterrupted />)

    await screen.findByText('최종 보고서')
    expect(screen.getByRole('button', { name: '최종 보고서 편집' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '최종 보고서 저장' })).toBeDisabled()
    expect(screen.getByText('실행 중에는 편집할 수 없습니다')).toBeInTheDocument()
  })
})
