import { fireEvent } from '@testing-library/react'
import { render, screen, userEvent, waitFor } from '../../test-utils'
import { PrincipalPicker } from '@/components/sharing/principal-picker'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'
import { orgApiMock } from '../organizations/org-admin-mocks'
import { trapConsole } from './sharing-mocks'

vi.mock(
  '@/lib/api/organizations',
  async () => (await import('../organizations/org-admin-mocks')).orgApiMock,
)

const { meOrganizationsApi, orgAdminApi } = orgApiMock

const RESULTS = [
  { type: 'user', id: 'u1', display_name: 'Kim', email: 'kim@example.com', member_count: null },
  { type: 'group', id: 'g1', display_name: 'Kimchi team', email: null, member_count: 3 },
  { type: 'org', id: 'org-1', display_name: 'Alpha', email: null, member_count: null },
]

describe('PrincipalPicker', () => {
  let consoleTrap: ReturnType<typeof trapConsole>

  beforeEach(() => {
    vi.clearAllMocks()
    consoleTrap = trapConsole()
    meOrganizationsApi.list.mockResolvedValue([makeOrganization()])
    orgAdminApi.searchPrincipals.mockImplementation(
      async (_org: string, _q: string, types: readonly string[]) =>
        RESULTS.filter((result) => types.includes(result.type)),
    )
  })

  afterEach(() => {
    consoleTrap.assertClean()
    consoleTrap.restore()
  })

  it('exposes combobox semantics and moves the active option with the arrows', async () => {
    const onPick = vi.fn()
    render(<PrincipalPicker types={['user', 'group', 'org']} label="대상" onPick={onPick} />)
    const combobox = await screen.findByRole('combobox', { name: '대상' })
    await waitFor(() => expect(combobox).toBeEnabled())
    expect(combobox).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('두 글자 이상 입력하세요.')).toBeInTheDocument()

    await userEvent.type(combobox, 'Ki')
    const options = await screen.findAllByRole('option')
    expect(combobox).toHaveAttribute('aria-expanded', 'true')
    expect(options).toHaveLength(3)
    expect(combobox).toHaveAttribute('aria-activedescendant', options[0].id)
    expect(screen.getByText('그룹 · 3명')).toBeInTheDocument()

    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(combobox).toHaveAttribute('aria-activedescendant', options[2].id)
    expect(options[2]).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowDown}')
    expect(combobox).toHaveAttribute('aria-activedescendant', options[0].id)
    await userEvent.keyboard('{ArrowUp}{Enter}')

    // `org` search results become `organization` grant principals.
    expect(onPick).toHaveBeenCalledWith({ type: 'organization', id: 'org-1', displayName: 'Alpha' })
    expect(combobox).toHaveValue('')
    expect(orgAdminApi.searchPrincipals).toHaveBeenCalledTimes(1)
    expect(orgAdminApi.searchPrincipals).toHaveBeenCalledWith('org-1', 'Ki', [
      'user',
      'group',
      'org',
    ])
  })

  it('hides principals that already have access and picks by click', async () => {
    const onPick = vi.fn()
    render(
      <PrincipalPicker
        types={['user', 'group']}
        excludeKeys={new Set(['user:u1'])}
        label="대상"
        onPick={onPick}
      />,
    )
    const combobox = await screen.findByRole('combobox', { name: '대상' })
    await waitFor(() => expect(combobox).toBeEnabled())
    await userEvent.type(combobox, 'Kim')
    const options = await screen.findAllByRole('option')
    expect(options).toHaveLength(1)
    expect(options[0]).toHaveTextContent('Kimchi team')
    await userEvent.click(screen.getByRole('option', { name: /Kimchi team/ }))
    expect(onPick).toHaveBeenCalledWith({ type: 'group', id: 'g1', displayName: 'Kimchi team' })
  })

  it('closes the list on Escape without letting the key reach a parent dialog', async () => {
    const parentKeyDown = vi.fn()
    render(
      <div onKeyDown={parentKeyDown}>
        <PrincipalPicker types={['user']} label="대상" onPick={vi.fn()} />
      </div>,
    )
    const combobox = await screen.findByRole('combobox', { name: '대상' })
    await waitFor(() => expect(combobox).toBeEnabled())
    await userEvent.type(combobox, 'Kim')
    await screen.findAllByRole('option')
    parentKeyDown.mockClear()
    fireEvent.keyDown(combobox, { key: 'Escape' })
    expect(parentKeyDown).not.toHaveBeenCalled()
    expect(combobox).toHaveValue('')
    expect(combobox).toHaveAttribute('aria-expanded', 'false')
  })

  it('is disabled with a hint when no organization is active', async () => {
    meOrganizationsApi.list.mockResolvedValue([])
    render(<PrincipalPicker types={['user']} label="대상" onPick={vi.fn()} />)
    expect(await screen.findByText('조직을 선택한 뒤 공유할 수 있어요.')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '대상' })).toBeDisabled()
  })
})
