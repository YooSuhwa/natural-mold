import { render } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import type { MessagesEnvelope } from '@/lib/types'

const appendMock = vi.fn()
let mockThreadEmpty = true

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({ thread: () => ({ append: appendMock }) }),
  useAuiState: (selector: (s: { thread?: { isEmpty: boolean } }) => unknown) =>
    selector({ thread: { isEmpty: mockThreadEmpty } }),
}))

import { SkillBuilderAutoRequest, resolveAutoFirstMessage } from '../skill-builder-auto-request'

const ENVELOPE_EMPTY = { messages: [], active_run: null, latest_run: null } as MessagesEnvelope

function session(overrides: Partial<{ status: string; user_request: string }> = {}) {
  return {
    status: 'active',
    user_request: '회의록 스킬을 만들어줘',
    ...overrides,
  } as Parameters<typeof resolveAutoFirstMessage>[0]
}

describe('resolveAutoFirstMessage', () => {
  it('활성 세션 + 빈 대화 + run 이력 없음이면 user_request를 반환한다', () => {
    expect(resolveAutoFirstMessage(session(), ENVELOPE_EMPTY)).toBe('회의록 스킬을 만들어줘')
  })

  it('envelope 쿼리가 아직 해결되지 않았으면 발화하지 않는다', () => {
    expect(resolveAutoFirstMessage(session(), undefined)).toBeNull()
  })

  it('대화 이력이 있으면(리로드) 발화하지 않는다', () => {
    const envelope = { ...ENVELOPE_EMPTY, messages: [{ id: 'm1' }] } as MessagesEnvelope
    expect(resolveAutoFirstMessage(session(), envelope)).toBeNull()
  })

  it('run 이력이 있으면(진행 중이거나 종료된 런) 발화하지 않는다', () => {
    const active = { ...ENVELOPE_EMPTY, active_run: { id: 'r1' } } as MessagesEnvelope
    const latest = { ...ENVELOPE_EMPTY, latest_run: { id: 'r1' } } as MessagesEnvelope
    expect(resolveAutoFirstMessage(session(), active)).toBeNull()
    expect(resolveAutoFirstMessage(session(), latest)).toBeNull()
  })

  it('active가 아닌 세션(completed/confirming 재진입)이면 발화하지 않는다', () => {
    expect(resolveAutoFirstMessage(session({ status: 'completed' }), ENVELOPE_EMPTY)).toBeNull()
    expect(resolveAutoFirstMessage(session({ status: 'confirming' }), ENVELOPE_EMPTY)).toBeNull()
  })

  it('user_request가 공백이면 발화하지 않는다', () => {
    expect(resolveAutoFirstMessage(session({ user_request: '  ' }), ENVELOPE_EMPTY)).toBeNull()
    expect(resolveAutoFirstMessage(undefined, ENVELOPE_EMPTY)).toBeNull()
  })
})

describe('SkillBuilderAutoRequest', () => {
  beforeEach(() => {
    appendMock.mockClear()
    mockThreadEmpty = true
  })

  it('text가 있으면 user message를 정확히 1회 append한다', () => {
    const { rerender } = render(<SkillBuilderAutoRequest text="스킬 만들어줘" />)
    expect(appendMock).toHaveBeenCalledTimes(1)
    expect(appendMock).toHaveBeenCalledWith({
      content: [{ type: 'text', text: '스킬 만들어줘' }],
    })

    // 부모 리렌더(envelope가 여전히 stale-empty)에도 재발화하지 않는다 (ref latch).
    rerender(<SkillBuilderAutoRequest text="스킬 만들어줘" />)
    expect(appendMock).toHaveBeenCalledTimes(1)
  })

  it('text가 null이면 append하지 않는다', () => {
    render(<SkillBuilderAutoRequest text={null} />)
    expect(appendMock).not.toHaveBeenCalled()
  })

  it('라이브 thread가 비어있지 않으면 append하지 않는다 (remount 이중 발화 방어)', () => {
    mockThreadEmpty = false
    render(<SkillBuilderAutoRequest text="스킬 만들어줘" />)
    expect(appendMock).not.toHaveBeenCalled()
  })

  it('thread가 나중에 빈 상태로 전이되면(가드 해제) 그때 1회 발화한다', () => {
    // 프로덕션의 실제 트리거 경로 — isThreadEmpty가 초기 false(하이드레이션 전)
    // 에서 true(thread ready & empty)로 바뀌는 순간 effect가 재실행되어 발화.
    mockThreadEmpty = false
    const { rerender } = render(<SkillBuilderAutoRequest text="스킬 만들어줘" />)
    expect(appendMock).not.toHaveBeenCalled()

    mockThreadEmpty = true
    rerender(<SkillBuilderAutoRequest text="스킬 만들어줘" />)
    expect(appendMock).toHaveBeenCalledTimes(1)
  })
})
