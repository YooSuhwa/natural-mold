import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'

type ToolPart = { readonly result?: unknown }
type AuiState = { readonly message?: { readonly parts?: readonly ToolPart[] } }

const mocks = vi.hoisted(() => ({
  state: { message: { parts: [] as readonly ToolPart[] } } as {
    message: { parts: readonly ToolPart[] }
  },
}))

// ToolGroupContainer가 쓰는 유일한 aui API는 useAuiState다. 검색 그룹의 출처 집계는
// `s.message.parts[i].result`를 읽으므로, parts를 직접 주입할 수 있게 mock한다.
vi.mock('@assistant-ui/react', () => ({
  useAuiState: <T,>(selector: (state: AuiState) => T): T => selector(mocks.state),
}))

// mock 등록 이후에 import해야 컴포넌트가 mock된 useAuiState를 본다.
const { ToolGroupContainer } = await import('../tool-group-container')

/** tavily 모양 결과 — results:[{title,url}]. */
function tavilyResult(urls: readonly string[]): unknown {
  return { results: urls.map((url, i) => ({ title: `r${i}`, url })) }
}

function setParts(parts: readonly ToolPart[]) {
  mocks.state.message.parts = parts
}

describe('ToolGroupContainer', () => {
  beforeEach(() => {
    setParts([])
  })

  it('알려진 도구명은 i18n 라벨로 표시하고 개수 라벨을 함께 보여준다', () => {
    render(
      <ToolGroupContainer toolName="tavily_search" count={10} running={false} indices={[]}>
        <div data-testid="child">leaf</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('웹 검색')).toBeInTheDocument()
    expect(screen.getByText('10회')).toBeInTheDocument()
  })

  it('라벨 매핑이 없는 도구는 toolName 자체를 라벨로 쓴다', () => {
    render(
      <ToolGroupContainer toolName="some_custom_tool" count={3} running={false} indices={[]}>
        <div>leaf</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('some_custom_tool')).toBeInTheDocument()
    expect(screen.getByText('3회')).toBeInTheDocument()
  })

  it('running=true면 기본 펼침이라 children이 보인다', () => {
    render(
      <ToolGroupContainer toolName="read_file" count={2} running={true} indices={[0, 1]}>
        <div data-testid="leaf">파일 내용</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.getByTestId('leaf')).toBeInTheDocument()
  })

  it('running=false(done)면 기본 접힘이라 children이 숨겨진다', () => {
    render(
      <ToolGroupContainer toolName="read_file" count={2} running={false} indices={[0, 1]}>
        <div data-testid="leaf">파일 내용</div>
      </ToolGroupContainer>,
    )
    expect(screen.getByText('파일 읽기')).toBeInTheDocument()
    expect(screen.queryByTestId('leaf')).not.toBeInTheDocument()
  })

  describe('검색 그룹 출처 집계 (LITE)', () => {
    it('여러 도메인 결과를 합쳐 고유 도메인 배지 + "출처 N개"를 보여준다', () => {
      // 3회 검색 — domain 4종(s/r/v/n), URL 9개(중복 없음)
      setParts([
        { result: tavilyResult(['https://s.com/a', 'https://r.com/b', 'https://v.com/c']) },
        { result: tavilyResult(['https://n.com/d', 'https://s.com/e', 'https://r.com/f']) },
        { result: tavilyResult(['https://v.com/g', 'https://n.com/h', 'https://s.com/i']) },
      ])
      render(
        <ToolGroupContainer toolName="tavily_search" count={3} running={false} indices={[0, 1, 2]}>
          <div>leaf</div>
        </ToolGroupContainer>,
      )
      // 고유 URL 9개 → "출처 9개"
      expect(screen.getByText('출처 9개')).toBeInTheDocument()
      // 고유 도메인 4종 → 배지는 최대 3개만(S/R/V — s가 3회로 최빈)
      expect(screen.getByText('S')).toBeInTheDocument()
      expect(screen.getByText('R')).toBeInTheDocument()
      expect(screen.getByText('V')).toBeInTheDocument()
      // 개수 라벨도 함께
      expect(screen.getByText('3회')).toBeInTheDocument()
    })

    it('URL 중복은 dedup되어 출처 수가 정확하다', () => {
      setParts([
        { result: tavilyResult(['https://a.com/1', 'https://b.com/2']) },
        { result: tavilyResult(['https://a.com/1', 'https://b.com/2']) }, // 동일 URL 2회
      ])
      render(
        <ToolGroupContainer toolName="web_search" count={2} running={false} indices={[0, 1]}>
          <div>leaf</div>
        </ToolGroupContainer>,
      )
      expect(screen.getByText('출처 2개')).toBeInTheDocument()
    })

    it('running=true(진행 중)면 출처 행을 띄우지 않는다', () => {
      setParts([{ result: tavilyResult(['https://a.com/1']) }])
      render(
        <ToolGroupContainer toolName="tavily_search" count={2} running={true} indices={[0, 1]}>
          <div>leaf</div>
        </ToolGroupContainer>,
      )
      expect(screen.queryByText(/출처/)).not.toBeInTheDocument()
    })

    it('비-검색 그룹(read_file)은 출처 행을 띄우지 않는다', () => {
      // read_file이지만 parts에 검색 모양 result가 있어도 집계하면 안 된다.
      setParts([
        { result: tavilyResult(['https://a.com/1', 'https://b.com/2']) },
        { result: tavilyResult(['https://c.com/3']) },
      ])
      render(
        <ToolGroupContainer toolName="read_file" count={2} running={false} indices={[0, 1]}>
          <div>leaf</div>
        </ToolGroupContainer>,
      )
      expect(screen.getByText('파일 읽기')).toBeInTheDocument()
      expect(screen.queryByText(/출처/)).not.toBeInTheDocument()
      // 도메인 배지도 없어야 한다
      expect(screen.queryByText('A')).not.toBeInTheDocument()
    })
  })
})
