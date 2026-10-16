import { render, screen, userEvent, waitFor } from '../../test-utils'
import { OrganizationSwitcher } from '@/components/layout/organization-switcher'
import { meOrganizationsApi } from '@/lib/api/organizations'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'

const toastError = vi.hoisted(() => vi.fn())

vi.mock('sonner', () => ({ toast: { error: toastError } }))

vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(), setActive: vi.fn() },
  tenantOrganizationsApi: {},
}))

vi.mock('@/components/ui/sidebar', () => ({
  SidebarGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarGroupContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SidebarMenu: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
  SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
  SidebarMenuButton: (
    props: React.ButtonHTMLAttributes<HTMLButtonElement> & { tooltip?: string },
  ) => {
    const buttonProps = { ...props }
    delete buttonProps.tooltip
    return <button type="button" {...buttonProps} />
  },
}))

const ALPHA = makeOrganization()
const BETA = makeOrganization({ id: 'org-2', name: 'Beta', is_default: false })
const SUSPENDED = makeOrganization({
  id: 'org-3',
  name: 'Gamma',
  tenant_name: 'Other Co',
  tenant_status: 'suspended',
})

describe('OrganizationSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('renders nothing when the user has no organizations', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([])
    const { container } = render(<OrganizationSwitcher />)
    await waitFor(() => expect(meOrganizationsApi.list).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the active organization and switches on selection', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA, SUSPENDED])
    vi.mocked(meOrganizationsApi.setActive).mockResolvedValue({
      org_id: 'org-2',
      tenant_id: 'tenant-1',
    })
    render(<OrganizationSwitcher />)

    const trigger = await screen.findByTestId('organization-switcher-trigger')
    expect(trigger).toHaveTextContent('Alpha')
    expect(trigger).toHaveTextContent('Hancom')

    await userEvent.click(trigger)
    expect(await screen.findByText('사용할 수 없는 회사')).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('menuitem', { name: /Beta/ }))

    await waitFor(() => expect(meOrganizationsApi.setActive).toHaveBeenCalledWith('org-2'))
    await waitFor(() =>
      expect(screen.getByTestId('organization-switcher-trigger')).toHaveTextContent('Beta'),
    )
  })

  it('does not call the API when the current organization is re-selected', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA])
    render(<OrganizationSwitcher />)
    await userEvent.click(await screen.findByTestId('organization-switcher-trigger'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Alpha/ }))
    expect(meOrganizationsApi.setActive).not.toHaveBeenCalled()
  })

  it('toasts when switching fails', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA])
    vi.mocked(meOrganizationsApi.setActive).mockRejectedValue(new Error('forbidden'))
    render(<OrganizationSwitcher />)
    await userEvent.click(await screen.findByTestId('organization-switcher-trigger'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Beta/ }))
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('조직을 전환하지 못했습니다. 다시 시도해 주세요.'),
    )
    expect(screen.getByTestId('organization-switcher-trigger')).toHaveTextContent('Alpha')
  })
})
