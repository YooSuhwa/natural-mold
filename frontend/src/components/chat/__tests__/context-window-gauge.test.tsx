import { describe, expect, it } from 'vitest'
import type { TokenUsageBreakdown } from '@/lib/types'
import { render, screen } from '../../../../tests/test-utils'
import { ContextWindowGauge } from '../context-window-gauge'

function usage(over: Partial<TokenUsageBreakdown>): TokenUsageBreakdown {
  return {
    prompt_tokens: 0,
    completion_tokens: 0,
    cache_creation_tokens: 0,
    cache_read_tokens: 0,
    ...over,
  }
}

describe('ContextWindowGauge', () => {
  it('점유량은 prompt_tokens 단독 — cache_*를 더하지 않는다', () => {
    // prompt 700k + cache 300k. cache를 더하면 100%가 되지만 prompt 단독이라 70%.
    render(
      <ContextWindowGauge
        usage={usage({ prompt_tokens: 700000, cache_creation_tokens: 200000, cache_read_tokens: 100000 })}
        contextWindow={1000000}
        modelName="claude-sonnet-4-6"
      />,
    )
    const trigger = screen.getByRole('button')
    expect(trigger.textContent).toContain('70%')
    expect(trigger.textContent).not.toContain('100%')
    expect(trigger.textContent).toContain('claude-sonnet-4-6')
  })

  it('< 80%: 중립색(경고색 아님)', () => {
    const { container } = render(
      <ContextWindowGauge usage={usage({ prompt_tokens: 500000 })} contextWindow={1000000} />,
    )
    expect(container.querySelector('.text-status-warn')).toBeNull()
    expect(container.querySelector('.text-status-danger')).toBeNull()
  })

  it('≥ 80%: 경고색(--status-warn)', () => {
    const { container } = render(
      <ContextWindowGauge usage={usage({ prompt_tokens: 850000 })} contextWindow={1000000} />,
    )
    expect(container.querySelector('.text-status-warn')).not.toBeNull()
    expect(container.querySelector('.text-status-danger')).toBeNull()
  })

  it('≥ 95%: 위험색(--status-danger)', () => {
    const { container } = render(
      <ContextWindowGauge usage={usage({ prompt_tokens: 980000 })} contextWindow={1000000} />,
    )
    expect(container.querySelector('.text-status-danger')).not.toBeNull()
  })

  it('context_window null: 숨기지 않고 비활성 + "한도 미설정" 표시', () => {
    render(
      <ContextWindowGauge usage={usage({ prompt_tokens: 500000 })} contextWindow={null} modelName="scripted" />,
    )
    const trigger = screen.getByRole('button')
    expect(trigger).toBeInTheDocument() // 숨기지 않음
    expect(trigger.textContent).toContain('한도 미설정')
    expect(trigger).toHaveAttribute('aria-label', '컨텍스트 한도 미설정')
    // 점유율(%) 텍스트는 없다.
    expect(trigger.textContent).not.toContain('%')
  })

  it('usage null(첫 턴 전): 0%로 빈 상태', () => {
    render(<ContextWindowGauge usage={null} contextWindow={1000000} />)
    expect(screen.getByRole('button').textContent).toContain('0%')
  })
})
