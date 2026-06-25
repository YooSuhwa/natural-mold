import type { ReactNode } from 'react'
import type { PartState } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../tests/test-utils'
import { groupAssistantParts, renderGroupedAssistantPart } from '../assistant-thread'

function toolCallPart(toolName: string): PartState {
  return {
    type: 'tool-call',
    toolName,
    toolCallId: `tc-${toolName}`,
    args: {},
    status: { type: 'complete' },
  } as unknown as PartState
}

function textPart(): PartState {
  return { type: 'text', text: 'hi', status: { type: 'complete' } } as unknown as PartState
}

/** 한 메시지의 parts 시퀀스를 groupBy로 돌려, 인접 같은 key를 합쳐 그룹 경계를
 * 만든다 — 공식 buildGroupTree와 동일한 인접 합치기 규칙을 작게 재현한 테스트 헬퍼. */
function groupKeysFor(parts: PartState[]): (string | null)[] {
  return parts.map((p) => {
    const path = groupAssistantParts(p)
    return path ? path[0] : null
  })
}

/** group-tool 노드 합성 — count(=indices 길이)와 running 상태를 받아 render fn에 넘긴다. */
function renderGroupNode(toolName: string, count: number, running: boolean, children: ReactNode) {
  const node = {
    type: `group-tool:${toolName}` as `group-${string}`,
    status: { type: running ? 'running' : 'complete' },
    indices: Array.from({ length: count }, (_, i) => i),
  }
  return render(<>{renderGroupedAssistantPart({ part: node, children })}</>)
}

describe('groupAssistantParts (groupBy)', () => {
  it('tool-call은 group-tool:<toolName> 경로, 비-tool part는 null', () => {
    expect(groupAssistantParts(toolCallPart('tavily_search'))).toEqual(['group-tool:tavily_search'])
    expect(groupAssistantParts(textPart())).toBeNull()
  })

  it('그룹 제외 도구(ask_user 등)는 null이라 그룹되지 않는다', () => {
    expect(groupAssistantParts(toolCallPart('ask_user'))).toBeNull()
    expect(groupAssistantParts(toolCallPart('request_approval'))).toBeNull()
  })

  it('intra-message: 같은 도구는 같은 key, 다른 도구는 다른 key로 분리된다', () => {
    // tavily ×3 + read_file ×1 (실측 데이터 패턴) → 2개의 서로 다른 그룹 key
    const parts = [
      toolCallPart('tavily_search'),
      toolCallPart('tavily_search'),
      toolCallPart('tavily_search'),
      toolCallPart('read_file'),
    ]
    const keys = groupKeysFor(parts)
    expect(keys).toEqual([
      'group-tool:tavily_search',
      'group-tool:tavily_search',
      'group-tool:tavily_search',
      'group-tool:read_file',
    ])
    const distinct = new Set(keys.filter((k): k is string => k !== null))
    expect(distinct.size).toBe(2)
  })
})

describe('renderGroupedAssistantPart (group-tool node)', () => {
  it('N≥2: 컨테이너로 묶고 라벨 + 개수를 보여준다', () => {
    renderGroupNode('tavily_search', 2, false, <div data-testid="leaf">leaf</div>)
    expect(screen.getByText('웹 검색')).toBeInTheDocument()
    expect(screen.getByText('2회')).toBeInTheDocument()
  })

  it('N=1: 컨테이너 없이 children만 패스스루(라벨/개수 없음)', () => {
    renderGroupNode('tavily_search', 1, false, <div data-testid="leaf">leaf</div>)
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
    expect(screen.queryByText('웹 검색')).not.toBeInTheDocument()
    expect(screen.queryByText('1회')).not.toBeInTheDocument()
  })

  it('running=true: 펼침 상태라 그룹 내부 children이 보인다', () => {
    renderGroupNode('read_file', 3, true, <div data-testid="leaf">leaf</div>)
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.getByText('3회')).toBeInTheDocument()
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
  })

  it('done(running=false): 접힘 상태라 그룹 내부 children이 숨겨진다', () => {
    renderGroupNode('read_file', 3, false, <div data-testid="leaf">leaf</div>)
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.queryByTestId('leaf')).not.toBeInTheDocument()
  })

  it('indicator part는 null (별도 로딩 인디케이터가 따로 렌더됨)', () => {
    const { container } = render(
      <>{renderGroupedAssistantPart({ part: { type: 'indicator' }, children: null })}</>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
