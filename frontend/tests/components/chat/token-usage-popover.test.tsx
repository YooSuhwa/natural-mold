import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '../../test-utils'
import userEvent from '@testing-library/user-event'

// useAuiState mock — 테스트별로 mockReturnValue로 교체.
const mockUseAssistantState = vi.fn()

vi.mock('@assistant-ui/react', () => ({
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({
      message: {
        metadata: {
          custom: { usage: mockUseAssistantState() },
        },
      },
    }),
}))

import { TokenUsagePopover } from '@/components/chat/token-usage-popover'

describe('TokenUsagePopover', () => {
  it('usage가 없으면 렌더하지 않는다', () => {
    mockUseAssistantState.mockReturnValue(undefined)
    const { container } = render(<TokenUsagePopover />)
    expect(container).toBeEmptyDOMElement()
  })

  it('총 토큰이 0이면 렌더하지 않는다', () => {
    mockUseAssistantState.mockReturnValue({
      prompt_tokens: 0,
      completion_tokens: 0,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    })
    const { container } = render(<TokenUsagePopover />)
    expect(container).toBeEmptyDOMElement()
  })

  it('총 토큰을 버튼에 표시한다', () => {
    mockUseAssistantState.mockReturnValue({
      prompt_tokens: 1200,
      completion_tokens: 300,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    })
    render(<TokenUsagePopover />)
    // 1200 + 300 = 1,500
    expect(screen.getByText('1,500')).toBeInTheDocument()
  })

  it('hover 시 4종 분해를 모두 노출한다', async () => {
    mockUseAssistantState.mockReturnValue({
      prompt_tokens: 1200,
      completion_tokens: 300,
      cache_creation_tokens: 800,
      cache_read_tokens: 200,
      estimated_cost: 0.0123,
    })
    const user = userEvent.setup()
    render(<TokenUsagePopover />)

    // hover로 popover 열기 — click은 onMouseEnter가 먼저 open(true)을 셋한 뒤
    // button onClick이 다시 toggle해서 닫혀버린다. 실 사용에서도 hover가 주 진입.
    await user.hover(screen.getByRole('button', { name: '토큰 사용량 보기' }))

    expect(screen.getByText('토큰 사용량')).toBeInTheDocument()
    expect(screen.getByText('1,200')).toBeInTheDocument() // input
    expect(screen.getByText('300')).toBeInTheDocument() // output
    expect(screen.getByText('800')).toBeInTheDocument() // cache_creation
    expect(screen.getByText('200')).toBeInTheDocument() // cache_read
    expect(screen.getByText('$0.0123')).toBeInTheDocument()
  })

  it('상세 팝오버는 부모 overflow에 잘리지 않도록 포털로 렌더한다', async () => {
    mockUseAssistantState.mockReturnValue({
      prompt_tokens: 1200,
      completion_tokens: 300,
      cache_creation_tokens: 800,
      cache_read_tokens: 200,
      estimated_cost: 0.0123,
    })
    const user = userEvent.setup()
    const { container } = render(
      <div className="overflow-hidden">
        <TokenUsagePopover />
      </div>,
    )

    await user.hover(screen.getByRole('button', { name: '토큰 사용량 보기' }))

    const tooltip = screen.getByRole('tooltip')
    expect(tooltip).toHaveTextContent('토큰 사용량')
    expect(container).not.toContainElement(tooltip)
  })

  it('estimated_cost가 0이면 비용 행을 숨긴다', async () => {
    mockUseAssistantState.mockReturnValue({
      prompt_tokens: 100,
      completion_tokens: 50,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      estimated_cost: 0,
    })
    const user = userEvent.setup()
    render(<TokenUsagePopover />)
    await user.hover(screen.getByRole('button', { name: '토큰 사용량 보기' }))
    expect(screen.queryByText('추정 비용')).not.toBeInTheDocument()
  })
})
