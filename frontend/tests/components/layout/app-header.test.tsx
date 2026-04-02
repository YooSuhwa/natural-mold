import { render } from '../../test-utils'
import { AppHeader } from '@/components/layout/app-header'

vi.mock('@/components/ui/sidebar', () => ({
  SidebarTrigger: (props: Record<string, unknown>) => (
    <button aria-label={props['aria-label'] as string} />
  ),
}))

vi.mock('@/components/ui/separator', () => ({
  Separator: () => <hr />,
}))

describe('AppHeader', () => {
  it('renders header element', () => {
    const { container } = render(<AppHeader />)
    const header = container.querySelector('header')
    expect(header).toBeInTheDocument()
  })

  it('renders sidebar trigger with correct aria label', () => {
    const { container } = render(<AppHeader />)
    const trigger = container.querySelector('[aria-label="사이드바 열기/닫기"]')
    expect(trigger).toBeInTheDocument()
  })
})
