import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { StatsCard } from '../stats-card'

describe('StatsCard', () => {
  it('renders KPI items with label, value, unit, and delta', () => {
    render(
      <StatsCard
        items={[
          { label: '총 요청', value: 1240, delta: 12 },
          { label: '성공률', value: 98.6, unit: '%', delta: -8 },
        ]}
      />,
    )
    expect(screen.getByTestId('data-ui-stats')).toBeInTheDocument()
    expect(screen.getByText('총 요청')).toBeInTheDocument()
    expect(screen.getByText('1,240')).toBeInTheDocument()
    expect(screen.getByText('98.6%')).toBeInTheDocument()
    expect(screen.getByText('12%')).toBeInTheDocument()
    expect(screen.getByText('8%')).toBeInTheDocument()
  })

  it('renders a string value without a delta', () => {
    render(<StatsCard items={[{ label: '상태', value: '정상' }]} />)
    expect(screen.getByText('상태')).toBeInTheDocument()
    expect(screen.getByText('정상')).toBeInTheDocument()
  })
})
