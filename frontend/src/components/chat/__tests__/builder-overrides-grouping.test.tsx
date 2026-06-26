import type { ReactNode } from 'react'
import type { EnrichedPartState, PartState } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'

// 빌더 표면도 메인 v3와 동일한 GroupedParts groupBy/노드 판별을 공유한다(Phase-2b).
// ToolGroupContainer가 useAuiState로 message.parts를 읽고, BuilderAssistantTextPart는
// useMessagePartText로 텍스트를 읽으므로, aui provider 없는 단위 테스트에서 두 훅만
// mock한다(나머지 builder-overrides import는 실제 모듈 그대로).
const auiMocks = vi.hoisted(() => ({
  state: {
    message: {
      parts: [] as readonly { readonly result?: unknown }[],
      status: { type: 'complete' },
    },
  },
  partText: { text: '' } as { text: string } | null,
}))

vi.mock('@assistant-ui/react', async () => {
  const actual = await vi.importActual<typeof import('@assistant-ui/react')>('@assistant-ui/react')
  return {
    ...actual,
    useAuiState: (selector: (state: typeof auiMocks.state) => unknown) => selector(auiMocks.state),
    useMessagePartText: () => auiMocks.partText,
  }
})

const { renderBuilderGroupedPart } = await import('../builder-overrides')

/** group-tool 노드 합성 — count(=indices 길이)와 running 상태를 render fn에 넘긴다. */
function renderGroupNode(toolName: string, count: number, running: boolean, children: ReactNode) {
  const node = {
    type: `group-tool:${toolName}` as `group-${string}`,
    status: { type: running ? 'running' : 'complete' },
    indices: Array.from({ length: count }, (_, i) => i),
  }
  return render(<>{renderBuilderGroupedPart({ part: node, children })}</>)
}

describe('renderBuilderGroupedPart (group-tool 노드)', () => {
  it('N≥2: 그룹 컨테이너로 묶어 라벨 + 개수를 보여준다', () => {
    renderGroupNode('read_file', 2, false, <div data-testid="leaf">leaf</div>)
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.getByText('2회')).toBeInTheDocument()
  })

  it('N=1: 컨테이너 없이 children만 패스스루(라벨/개수 없음)', () => {
    renderGroupNode('read_file', 1, false, <div data-testid="leaf">leaf</div>)
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
    expect(screen.queryByText('파일 읽기')).not.toBeInTheDocument()
    expect(screen.queryByText('1회')).not.toBeInTheDocument()
  })

  it('running=true: 펼침 상태라 그룹 내부 children이 보인다', () => {
    renderGroupNode('read_file', 3, true, <div data-testid="leaf">leaf</div>)
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.getByText('3회')).toBeInTheDocument()
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
  })
})

describe('renderBuilderGroupedPart (leaf part)', () => {
  it('text part: phase-narration 파서를 거쳐 본문 텍스트를 보존 렌더', () => {
    auiMocks.partText = { text: '검색 결과를 정리했습니다.' }
    const textPart = { type: 'text', text: '검색 결과를 정리했습니다.' } as unknown as PartState
    render(<>{renderBuilderGroupedPart({ part: textPart as never, children: null })}</>)
    expect(screen.getByText('검색 결과를 정리했습니다.')).toBeInTheDocument()
  })

  it('text part: phase 전환 문구는 SystemEventChip(role=status)으로 변환', () => {
    auiMocks.partText = { text: '[Phase 2 완료]' }
    const textPart = { type: 'text', text: '[Phase 2 완료]' } as unknown as PartState
    render(<>{renderBuilderGroupedPart({ part: textPart as never, children: null })}</>)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('tool-call leaf: 등록된 per-tool UI(leaf.toolUI)를 우선 렌더', () => {
    const leaf = {
      type: 'tool-call',
      toolName: 'phase_timeline',
      toolCallId: 'tc-1',
      args: {},
      status: { type: 'complete' },
      toolUI: <div data-testid="registered">registered</div>,
    } as unknown as EnrichedPartState
    render(<>{renderBuilderGroupedPart({ part: leaf, children: null })}</>)
    expect(screen.getByTestId('registered')).toBeInTheDocument()
  })

  it('tool-call leaf: 미등록 도구는 BuilderToolFallback로 toolName을 안전망 표시', () => {
    const leaf = {
      type: 'tool-call',
      toolName: 'unknown_tool',
      toolCallId: 'tc-2',
      args: {},
      status: { type: 'complete' },
    } as unknown as EnrichedPartState
    render(<>{renderBuilderGroupedPart({ part: leaf, children: null })}</>)
    expect(screen.getByText('unknown_tool')).toBeInTheDocument()
  })

  it('indicator part는 null (별도 로딩 인디케이터가 따로 렌더됨)', () => {
    const { container } = render(
      <>{renderBuilderGroupedPart({ part: { type: 'indicator' }, children: null })}</>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
