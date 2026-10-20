import { CredentialPicker } from '@/components/credential/credential-picker'
import { render, screen, userEvent, waitFor } from '../../test-utils'

const credentials = vi.hoisted(() => ({
  list: vi.fn(),
  listCompanyShared: vi.fn(),
  listTypes: vi.fn(),
}))
vi.mock('@/lib/api/credentials', () => ({ credentialsApi: credentials, systemCredentialsApi: {} }))

const own = { id: 'own-1', name: 'My bearer', definition_key: 'http_bearer', status: 'active' }
const company = {
  id: 'co-1',
  name: 'Company bearer',
  definition_key: 'http_bearer',
  status: 'active',
}
const otherType = {
  id: 'co-2',
  name: 'Company OAuth',
  definition_key: 'mcp_oauth2',
  status: 'active',
}

describe('CredentialPicker company shared keys (P2-3)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    credentials.list.mockResolvedValue([own])
    credentials.listCompanyShared.mockResolvedValue([company, otherType])
  })

  it('offers designated company keys of the accepted type in their own group', async () => {
    const onChange = vi.fn()
    render(<CredentialPicker includeCompany definitionKeys={['http_bearer']} onChange={onChange} />)
    await waitFor(() => expect(credentials.listCompanyShared).toHaveBeenCalled())
    await userEvent.click(screen.getByRole('combobox'))
    expect(await screen.findByText('회사 공유 키')).toBeInTheDocument()
    expect(screen.getByText('내 자격증명')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Company OAuth' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('option', { name: 'Company bearer' }))
    expect(onChange).toHaveBeenCalledWith('co-1')
  })

  it('never asks for company keys unless the caller opts in', async () => {
    render(<CredentialPicker onChange={vi.fn()} />)
    await waitFor(() => expect(credentials.list).toHaveBeenCalled())
    expect(credentials.listCompanyShared).not.toHaveBeenCalled()
  })
})
