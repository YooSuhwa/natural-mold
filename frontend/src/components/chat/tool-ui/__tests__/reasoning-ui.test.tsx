import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { ReasoningDataUI, ReasoningDataView } from '../reasoning-ui'

describe('ReasoningDataUI', () => {
  it('registers the reasoning data renderer name', () => {
    expect(ReasoningDataUI.unstable_data.name).toBe('reasoning')
  })

  it('renders summary text as a labeled status panel', () => {
    render(
      <ReasoningDataView data={{ summary: '검토한 내용을 요약합니다.' }} statusType="complete" />,
    )

    expect(screen.getByText('추론 요약')).toBeInTheDocument()
    expect(screen.getByText('검토한 내용을 요약합니다.')).toBeInTheDocument()
    expect(screen.getByText('준비됨')).toBeInTheDocument()
  })

  it('does not render raw reasoning fields', () => {
    const data = { status: 'working', reasoning: 'hidden chain' }
    render(<ReasoningDataView data={data} statusType="running" />)

    expect(screen.getByText('추론 요약')).toBeInTheDocument()
    expect(screen.queryByText('working')).toBeInTheDocument()
    expect(screen.queryByText('hidden chain')).not.toBeInTheDocument()
  })
})
