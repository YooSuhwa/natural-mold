import type { AnchorHTMLAttributes, MouseEvent } from 'react'

import { SidebarBrandHeader } from '@/components/layout/sidebar-brand-header'
import { SidebarProvider, useSidebar } from '@/components/ui/sidebar'
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

vi.mock('next/image', () => ({
  default: ({ alt, className }: { alt: string; className?: string }) => (
    <span aria-label={alt} className={className} role="img" />
  ),
}))

function SidebarStateProbe() {
  const { state } = useSidebar()
  return <output data-testid="sidebar-state">{state}</output>
}

describe('SidebarBrandHeader', () => {
  it('renders a brand link with the configured home href', () => {
    render(
      <SidebarProvider>
        <SidebarBrandHeader brandLabel="Moldy" homeHref="/settings" toggleLabel="전환" />
      </SidebarProvider>,
    )

    expect(screen.getByRole('link', { name: 'Moldy' })).toHaveAttribute('href', '/settings')
    expect(screen.getAllByRole('img', { name: 'Moldy' })).toHaveLength(2)
  })

  it('toggles the sidebar when the header control is clicked', async () => {
    const user = userEvent.setup()

    render(
      <SidebarProvider>
        <SidebarBrandHeader brandLabel="Moldy" homeHref="/" toggleLabel="전환" />
        <SidebarStateProbe />
      </SidebarProvider>,
    )

    expect(screen.getByTestId('sidebar-state')).toHaveTextContent('expanded')

    await user.click(screen.getAllByRole('button', { name: '전환' })[0])

    expect(screen.getByTestId('sidebar-state')).toHaveTextContent('collapsed')
  })
})
