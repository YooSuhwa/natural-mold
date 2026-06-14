import { fireEvent } from '@testing-library/react'
import { render, screen, waitFor } from '../../test-utils'
import { Sidebar, SidebarProvider, SidebarRail, useSidebar } from '@/components/ui/sidebar'
import { beforeEach, describe, expect, it } from 'vitest'

function installAnimationFrameStub(): void {
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    },
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true,
    value: () => {},
  })
}

function SidebarProbe() {
  const { sidebarWidth, state } = useSidebar()
  return (
    <output data-testid="sidebar-probe" data-state={state}>
      {sidebarWidth}
    </output>
  )
}

function SidebarResizeHarness({
  defaultOpen = true,
  initialSidebarWidth = 256,
}: {
  defaultOpen?: boolean
  initialSidebarWidth?: number | null
}) {
  return (
    <SidebarProvider defaultOpen={defaultOpen} initialSidebarWidth={initialSidebarWidth}>
      <Sidebar collapsible="icon">
        <div>Sidebar body</div>
        <SidebarRail />
      </Sidebar>
      <SidebarProbe />
    </SidebarProvider>
  )
}

describe('resizable sidebar rail', () => {
  beforeEach(() => {
    installAnimationFrameStub()
    window.localStorage.clear()
    document.cookie = 'moldy_sidebar_width=; path=/; max-age=0'
    document.cookie = 'sidebar_state=; path=/; max-age=0'
  })

  it('uses the server-provided initial width for first paint', () => {
    const { container } = render(<SidebarResizeHarness initialSidebarWidth={300} />)
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')

    expect(wrapper).toHaveStyle({ '--sidebar-width': '300px' })
    expect(screen.getByTestId('sidebar-probe')).toHaveTextContent('300')
  })

  it('does not restore localStorage width during first paint without a server cookie', () => {
    window.localStorage.setItem('moldy.sidebar.widthPx', '360')

    const { container } = render(<SidebarResizeHarness initialSidebarWidth={null} />)
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')

    expect(wrapper).toHaveStyle({ '--sidebar-width': '256px' })
    expect(screen.getByTestId('sidebar-probe')).toHaveTextContent('256')
  })

  it('commits expanded drag width to localStorage and the width cookie', () => {
    const { container } = render(<SidebarResizeHarness initialSidebarWidth={256} />)
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')
    const handle = screen.getByRole('separator', { name: '사이드바 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 180, pointerId: 1 })

    expect(wrapper).toHaveStyle({ '--sidebar-width': '336px' })
    expect(document.documentElement).toHaveAttribute('data-panel-resizing', 'true')

    fireEvent.pointerUp(handle, { clientX: 180, pointerId: 1 })

    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBe('336')
    expect(document.cookie).toContain('moldy_sidebar_width=336')
    expect(document.documentElement).not.toHaveAttribute('data-panel-resizing')
  })

  it('collapses below the threshold without storing the preview width', async () => {
    const { container } = render(<SidebarResizeHarness initialSidebarWidth={256} />)
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')
    const handle = screen.getByRole('separator', { name: '사이드바 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: -20, pointerId: 1 })

    expect(wrapper).toHaveStyle({ '--sidebar-width': '136px' })
    expect(handle).toHaveAttribute('data-collapse-preview', 'true')

    fireEvent.pointerUp(handle, { clientX: -20, pointerId: 1 })

    await waitFor(() => {
      expect(screen.getByTestId('sidebar-probe')).toHaveAttribute('data-state', 'collapsed')
    })
    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBeNull()
  })

  it('reverts the transient preview width when pointer resize is canceled', () => {
    const { container } = render(<SidebarResizeHarness initialSidebarWidth={256} />)
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')
    const handle = screen.getByRole('separator', { name: '사이드바 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 180, pointerId: 1 })

    expect(wrapper).toHaveStyle({ '--sidebar-width': '336px' })

    fireEvent.pointerCancel(handle, { pointerId: 1 })

    expect(wrapper).toHaveStyle({ '--sidebar-width': '256px' })
    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBeNull()
    expect(document.documentElement).not.toHaveAttribute('data-panel-resizing')
  })

  it('rolls back a canceled drag-open from the collapsed rail', () => {
    const { container } = render(
      <SidebarResizeHarness defaultOpen={false} initialSidebarWidth={256} />,
    )
    const wrapper = container.querySelector('[data-slot="sidebar-wrapper"]')
    const handle = screen.getByRole('separator', { name: '사이드바 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 360, pointerId: 1 })

    expect(screen.getByTestId('sidebar-probe')).toHaveAttribute('data-state', 'expanded')
    expect(wrapper).toHaveStyle({ '--sidebar-width': '260px' })
    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBeNull()

    fireEvent.pointerCancel(handle, { pointerId: 1 })

    expect(screen.getByTestId('sidebar-probe')).toHaveAttribute('data-state', 'collapsed')
    expect(wrapper).toHaveStyle({ '--sidebar-width': '256px' })
    expect(window.localStorage.getItem('moldy.sidebar.widthPx')).toBeNull()
    expect(document.cookie).not.toContain('moldy_sidebar_width=')
    expect(document.documentElement).not.toHaveAttribute('data-panel-resizing')
  })

  it('keeps the collapsed rail separator value inside its announced aria bounds', () => {
    render(<SidebarResizeHarness defaultOpen={false} initialSidebarWidth={256} />)

    const handle = screen.getByRole('separator', { name: '사이드바 크기 조절' })

    expect(screen.getByTestId('sidebar-probe')).toHaveAttribute('data-state', 'collapsed')
    expect(handle).toHaveAttribute('aria-valuemin', '0')
    expect(handle).toHaveAttribute('aria-valuenow', '0')
    expect(handle).toHaveAttribute('aria-valuetext', '접힘')
  })
})
