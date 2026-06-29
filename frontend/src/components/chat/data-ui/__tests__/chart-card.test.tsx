import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { ChartCard } from '../chart-card'

const SERIES = [
  { label: 'Mon', value: 12 },
  { label: 'Tue', value: 19 },
  { label: 'Wed', value: 7 },
]

describe('ChartCard', () => {
  it('renders a bar chart with title, axis labels, and a rect per point', () => {
    const { container } = render(
      <ChartCard chartType="bar" series={SERIES} title="주간" xLabel="요일" yLabel="건수" />,
    )
    const card = screen.getByTestId('data-ui-chart')
    expect(card).toHaveAttribute('data-chart-type', 'bar')
    expect(screen.getByText('주간')).toBeInTheDocument()
    expect(screen.getByText('요일')).toBeInTheDocument()
    expect(screen.getByText('Mon')).toBeInTheDocument()
    expect(container.querySelectorAll('rect')).toHaveLength(SERIES.length)
  })

  it('renders a line chart as a polyline + point circles', () => {
    const { container } = render(<ChartCard chartType="line" series={SERIES} />)
    expect(screen.getByTestId('data-ui-chart')).toHaveAttribute('data-chart-type', 'line')
    expect(container.querySelectorAll('polyline')).toHaveLength(1)
    expect(container.querySelectorAll('circle')).toHaveLength(SERIES.length)
  })

  it('renders an empty state when the series is empty', () => {
    render(<ChartCard chartType="bar" series={[]} />)
    expect(screen.getByTestId('data-ui-chart-empty')).toBeInTheDocument()
  })
})
