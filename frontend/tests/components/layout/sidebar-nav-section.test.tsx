import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { CalendarClockIcon, UserIcon } from 'lucide-react'

import { SidebarNavSection } from '@/components/layout/sidebar-nav-section'
import type { SidebarNavSectionConfig } from '@/components/layout/sidebar-nav-section'
import { SidebarProvider } from '@/components/ui/sidebar'
import { render, screen, userEvent } from '../../test-utils'

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    onClick,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a
      href={href}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault()
        onClick?.(event)
      }}
      {...props}
    >
      {children}
    </a>
  ),
}))

const section = {
  label: '계정',
  items: [
    { href: '/settings', label: '프로필', icon: UserIcon, exact: true },
    {
      href: '/settings/schedules',
      label: '스케줄',
      icon: CalendarClockIcon,
      badge: 120,
    },
  ],
} satisfies SidebarNavSectionConfig

describe('SidebarNavSection', () => {
  it('marks the matching item as the current page', () => {
    render(
      <SidebarProvider>
        <SidebarNavSection
          menuClassName="moldy-sidebar-nav-item"
          pathname="/settings/schedules/archived"
          section={section}
        />
      </SidebarProvider>,
    )

    expect(screen.getByText('계정')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '스케줄' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('99+')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '프로필' })).not.toHaveAttribute('aria-current')
  })

  it('runs the navigation callback from rendered links', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()

    render(
      <SidebarProvider>
        <SidebarNavSection
          menuClassName="moldy-sidebar-nav-item"
          onNavigate={onNavigate}
          pathname="/settings"
          section={section}
        />
      </SidebarProvider>,
    )

    await user.click(screen.getByRole('link', { name: '스케줄' }))

    expect(onNavigate).toHaveBeenCalledOnce()
  })
})
