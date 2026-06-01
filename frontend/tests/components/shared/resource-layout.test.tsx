import { render, screen, within } from '../../test-utils'
import { CountedLineTabs, ResourceGrid, ResourcePanel } from '@/components/shared/resource-layout'

describe('resource layout primitives', () => {
  it('renders the active tab count inside the selected tab only', () => {
    render(
      <CountedLineTabs
        ariaLabel="Resource categories"
        value="all"
        onValueChange={() => undefined}
        tabs={[
          { value: 'all', label: '전체', countLabel: '7개' },
          { value: 'data', label: '데이터', countLabel: '2개' },
        ]}
      />,
    )

    const activeTab = screen.getByRole('tab', { name: '전체 7개' })
    const inactiveTab = screen.getByRole('tab', { name: '데이터' })

    expect(activeTab).toHaveAttribute('aria-selected', 'true')
    expect(within(activeTab).getByText('7개')).toBeInTheDocument()
    expect(inactiveTab).toHaveAttribute('aria-selected', 'false')
    expect(screen.queryByText('2개')).not.toBeInTheDocument()
  })

  it('keeps panel chrome separate from scrollable content', () => {
    render(
      <ResourcePanel>
        <ResourcePanel.Toolbar>toolbar</ResourcePanel.Toolbar>
        <ResourcePanel.Body>body</ResourcePanel.Body>
      </ResourcePanel>,
    )

    expect(screen.getByText('toolbar')).toHaveClass('shrink-0')
    expect(screen.getByText('body')).toHaveClass('overflow-y-auto')
  })

  it('provides a reusable grid shell without owning card markup', () => {
    render(
      <ResourceGrid minColumnWidth={220}>
        <article>custom card</article>
      </ResourceGrid>,
    )

    expect(screen.getByText('custom card')).toBeInTheDocument()
    expect(screen.getByText('custom card').parentElement).toHaveStyle({
      gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
    })
  })
})
