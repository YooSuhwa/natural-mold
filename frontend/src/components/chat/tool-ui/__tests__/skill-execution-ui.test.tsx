import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { ChatConversationContext } from '../../conversation-context'
import { outputFilesFromResult, skillNameFromDirectory } from '../skill-execution-ui'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

describe('skillNameFromDirectory', () => {
  it('가상 경로 마지막 세그먼트를 스킬 이름으로 뽑는다', () => {
    expect(skillNameFromDirectory('/runtime/thread-1/agents/a1/skills/openwiki')).toBe('openwiki')
    expect(skillNameFromDirectory('skills/data-report/')).toBe('data-report')
  })

  it('경로가 없거나 skills 디렉토리 자체면 null', () => {
    expect(skillNameFromDirectory(undefined)).toBeNull()
    expect(skillNameFromDirectory('/skills/')).toBeNull()
    expect(skillNameFromDirectory('')).toBeNull()
  })
})

describe('outputFilesFromResult', () => {
  it('OUTPUT_FILES 계약 라인에서 파일명을 추출한다', () => {
    expect(outputFilesFromResult('stdout...\n\nOUTPUT_FILES: report.md, chart.png')).toEqual([
      'report.md',
      'chart.png',
    ])
  })

  it('OUTPUT_FILES가 없으면 빈 배열', () => {
    expect(outputFilesFromResult('그냥 출력')).toEqual([])
    expect(outputFilesFromResult(undefined)).toEqual([])
    expect(outputFilesFromResult({ not: 'a string' })).toEqual([])
  })
})

describe('SkillExecutionToolUI render', () => {
  async function renderCard(props: {
    args: Record<string, unknown>
    result?: unknown
    statusType?: string
  }) {
    // makeAssistantToolUI 래퍼는 assistant-ui 런타임 컨텍스트가 필요하므로
    // 내부 render 함수를 직접 렌더한다 (SearchRender 테스트와 동일 접근).
    const { SkillExecutionToolUI } = await import('../skill-execution-ui')
    const renderFn = (
      SkillExecutionToolUI as unknown as {
        unstable_tool: { render: (p: unknown) => ReactNode }
      }
    ).unstable_tool.render
    // renderFn을 Provider "아래의" 컴포넌트 렌더 중에 호출해야
    // useChatConversationId가 provider 값을 읽는다 (Wrapper 본문에서 직접
    // 호출하면 provider 바깥 fiber에서 훅이 실행된다).
    function CardUnderTest() {
      return (
        <>
          {renderFn({
            toolName: 'execute_in_skill',
            toolCallId: 'call-1',
            args: props.args,
            argsText: JSON.stringify(props.args),
            result: props.result,
            status: { type: props.statusType ?? 'complete' },
          })}
        </>
      )
    }
    return render(
      <ChatConversationContext.Provider value="conv-77">
        <CardUnderTest />
      </ChatConversationContext.Provider>,
    )
  }

  it('스킬 이름을 제목으로, 파일 개수를 메타로 보여준다', async () => {
    await renderCard({
      args: {
        skill_directory: '/runtime/t/skills/data-report',
        command: 'python scripts/aggregate.py',
      },
      result: 'ok\n\nOUTPUT_FILES: out.csv',
    })
    expect(screen.getByText('data-report')).toBeInTheDocument()
    expect(screen.getByText('files(1)')).toBeInTheDocument()
  })

  it('펼치면 커맨드와 파일 링크(API 경로)를 보여준다', async () => {
    const user = userEvent.setup()
    await renderCard({
      args: {
        skill_directory: '/runtime/t/skills/data-report',
        command: 'python scripts/aggregate.py',
      },
      result: 'ok\n\nOUTPUT_FILES: out.csv, chart.png',
    })
    // 파일이 있으면 기본 펼침 — 링크가 바로 보인다.
    const link = screen.getByText('out.csv').closest('a')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toContain('/api/conversations/conv-77/files/out.csv')
    expect(screen.getByText('python scripts/aggregate.py')).toBeInTheDocument()
    // 접었다 펴도 유지.
    await user.click(screen.getByText('data-report'))
    expect(screen.queryByText('python scripts/aggregate.py')).not.toBeInTheDocument()
  })

  it('실행 중에는 running 메타를 보여주고 파일을 파싱하지 않는다', async () => {
    await renderCard({
      args: { skill_directory: '/runtime/t/skills/openwiki', command: 'python x.py' },
      statusType: 'running',
    })
    expect(screen.getByText('running')).toBeInTheDocument()
  })
})
