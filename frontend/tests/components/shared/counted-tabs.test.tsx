import { render, screen, userEvent, within } from '../../test-utils'
import { CountedTabs } from '@/components/shared/counted-tabs'

describe('CountedTabs', () => {
  it('renders counts and calls onValueChange', async () => {
    const user = userEvent.setup()
    const values: string[] = []

    render(
      <CountedTabs
        ariaLabel="리소스 상태"
        value="all"
        onValueChange={(value) => values.push(value)}
        tabs={[
          { value: 'all', label: '전체', count: 7 },
          { value: 'active', label: '활성', countLabel: '2개' },
        ]}
      />,
    )

    const allTab = screen.getByRole('tab', { name: '전체 7' })
    const activeTab = screen.getByRole('tab', { name: '활성 2개' })

    expect(within(allTab).getByText('7')).toBeInTheDocument()
    await user.click(activeTab)
    expect(values).toEqual(['active'])
  })

  it('keeps disabled tabs inert', async () => {
    const user = userEvent.setup()
    const values: string[] = []

    render(
      <CountedTabs
        ariaLabel="리소스 상태"
        value="all"
        onValueChange={(value) => values.push(value)}
        tabs={[
          { value: 'all', label: '전체' },
          { value: 'disabled', label: '비활성', disabled: true },
        ]}
      />,
    )

    await user.click(screen.getByRole('tab', { name: '비활성' }))
    expect(values).toEqual([])
  })
})
