import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { PlanToolUI } from '../plan-tool-ui'
import { UserInputUI } from '../user-input-ui'

// M8-4 회귀: 스트리밍 중 tool-call args는 부분 JSON으로 도착한다 — 배열 필드가
// 문자열/객체 조각인 순간에도 렌더가 호출되므로, 가드가 없으면 실 LLM 경로에서
// 렌더 크래시(에러 바운더리로 채팅 전체 다운)가 난다. scripted 모델은 완성
// args만 방출해 이 크래시를 재현하지 못한다 (실 LLM 투어에서 발견).

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type ToolUiRender = {
  render: (props: {
    args: Record<string, unknown>
    result?: unknown
    status: { type: string }
  }) => ReactNode
}

describe('부분 스트리밍 args 방어 (M8-4)', () => {
  it('write_todos: todos가 문자열 조각이어도 크래시 없이 렌더된다', () => {
    const toolUi = PlanToolUI as unknown as ToolUiRender
    expect(() =>
      render(
        <>{toolUi.render({ args: { todos: '회의록에서 담' }, status: { type: 'running' } })}</>,
      ),
    ).not.toThrow()
  })

  it('write_todos: item.status가 부분 문자열이면 pending으로 폴백한다', () => {
    const toolUi = PlanToolUI as unknown as ToolUiRender
    expect(() =>
      render(
        <>
          {toolUi.render({
            args: { todos: [{ content: '초안 작성', status: 'in_prog' }] },
            status: { type: 'running' },
          })}
        </>,
      ),
    ).not.toThrow()
  })

  it('write_todos: content 없는 조각 아이템은 걸러진다', () => {
    const toolUi = PlanToolUI as unknown as ToolUiRender
    expect(() =>
      render(
        <>
          {toolUi.render({
            args: { todos: [{}, { content: '검증 실행' }] },
            status: { type: 'running' },
          })}
        </>,
      ),
    ).not.toThrow()
  })

  it('ask_user: questions/options가 문자열 조각이어도 크래시 없이 렌더된다', () => {
    const toolUi = UserInputUI as unknown as ToolUiRender
    const hitl = { onResumeDecisions: vi.fn(), registerDecision: vi.fn() }
    // render fn이 훅을 직접 호출하므로 컴포넌트로 감싸 React render 단계에서 실행.
    function AskUserUnderTest() {
      return (
        <>
          {toolUi.render({
            args: { questions: '어떤 형식', options: '표' },
            status: { type: 'running' },
          })}
        </>
      )
    }
    expect(() =>
      render(
        <HiTLContext.Provider value={hitl}>
          <AskUserUnderTest />
        </HiTLContext.Provider>,
      ),
    ).not.toThrow()
  })
})
