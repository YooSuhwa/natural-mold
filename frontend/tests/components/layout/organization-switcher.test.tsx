import { render, screen, userEvent, waitFor } from '../../test-utils'
import { OrganizationSwitcher } from '@/components/layout/organization-switcher'
import { meOrganizationsApi } from '@/lib/api/organizations'
import { OrgContextProvider } from '@/lib/providers/org-context-provider'
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

function renderSwitcher() {
  return render(
    <OrgContextProvider>
      <OrganizationSwitcher />
    </OrgContextProvider>,
  )
}

const ALPHA = makeOrganization()
const BETA = makeOrganization({ id: 'org-2', name: 'Beta', is_default: false, is_active: false })
const SUSPENDED = makeOrganization({
  id: 'org-3',
  name: 'Gamma',
  tenant_name: 'Other Co',
  tenant_status: 'suspended',
  is_active: false,
})

describe('OrganizationSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nothing when the user has no organizations', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([])
    const { container } = renderSwitcher()
    await waitFor(() => expect(meOrganizationsApi.list).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the active organization and switches on selection', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA, SUSPENDED])
    vi.mocked(meOrganizationsApi.setActive).mockResolvedValue({
      org_id: 'org-2',
      tenant_id: 'tenant-1',
    })
    vi.mocked(meOrganizationsApi.list)
      .mockResolvedValueOnce([ALPHA, BETA, SUSPENDED])
      .mockResolvedValue([{ ...ALPHA, is_active: false }, { ...BETA, is_active: true }, SUSPENDED])
    renderSwitcher()

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
    renderSwitcher()
    await userEvent.click(await screen.findByTestId('organization-switcher-trigger'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Alpha/ }))
    expect(meOrganizationsApi.setActive).not.toHaveBeenCalled()
  })

  it('toasts when switching fails', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA, BETA])
    vi.mocked(meOrganizationsApi.setActive).mockRejectedValue(new Error('forbidden'))
    renderSwitcher()
    await userEvent.click(await screen.findByTestId('organization-switcher-trigger'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Beta/ }))
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('조직을 전환하지 못했습니다. 다시 시도해 주세요.'),
    )
    expect(screen.getByTestId('organization-switcher-trigger')).toHaveTextContent('Alpha')
  })
})

describe('OrganizationSwitcher with a single organization', () => {
  it('shows the current organization without a switch menu', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([ALPHA])
    renderSwitcher()
    const current = await screen.findByTestId('organization-switcher-current')
    expect(current).toHaveTextContent('Alpha')
    expect(current).toHaveTextContent('Hancom')
    expect(screen.queryByTestId('organization-switcher-trigger')).not.toBeInTheDocument()
  })
})

describe('OrganizationSwitcher without a resolvable active org', () => {
  it('prompts to choose an organization', async () => {
    vi.mocked(meOrganizationsApi.list).mockResolvedValue([{ ...ALPHA, is_active: false }])
    renderSwitcher()
    expect(await screen.findByTestId('organization-switcher-trigger')).toHaveTextContent(
      '조직 선택',
    )
  })
})
