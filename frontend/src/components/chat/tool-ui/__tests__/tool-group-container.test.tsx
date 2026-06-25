import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { ToolGroupContainer } from '../tool-group-container'

describe('ToolGroupContainer', () => {
  it('알려진 도구명은 i18n 라벨로 표시하고 개수 라벨을 함께 보여준다', () => {
    render(
      <ToolGroupContainer toolName="tavily_search" count={10} running={false}>
        <div data-testid="child">leaf</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('웹 검색')).toBeInTheDocument()
    expect(screen.getByText('10회')).toBeInTheDocument()
  })

  it('라벨 매핑이 없는 도구는 toolName 자체를 라벨로 쓴다', () => {
    render(
      <ToolGroupContainer toolName="some_custom_tool" count={3} running={false}>
        <div>leaf</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('some_custom_tool')).toBeInTheDocument()
    expect(screen.getByText('3회')).toBeInTheDocument()
  })

  it('running=true면 기본 펼침이라 children이 보인다', () => {
    render(
      <ToolGroupContainer toolName="read_file" count={2} running={true}>
        <div data-testid="leaf">파일 내용</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
  })

  it('running=false(done)면 기본 접힘이라 children이 숨겨진다', () => {
    render(
      <ToolGroupContainer toolName="read_file" count={2} running={false}>
        <div data-testid="leaf">파일 내용</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.queryByTestId('leaf')).not.toBeInTheDocument()
  })
})
