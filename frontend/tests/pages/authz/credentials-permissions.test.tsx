import { render, screen, waitFor } from '../../test-utils'
import { CredentialDetailDialog } from '@/components/credential/credential-detail-dialog'
import SystemCredentialsPage from '@/app/settings/system-credentials/page'
import type { Credential } from '@/lib/types/credential'

const state = vi.hoisted(() => ({
  credential: null as unknown,
  permissions: [] as { id: string; my_permissions: string[] }[],
  user: { id: 'user-me', is_super_user: false } as { id: string; is_super_user: boolean },
  replace: vi.fn(),
  systemList: vi.fn(),
}))

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredential: () => ({ data: state.credential, isLoading: false }),
  useCredentialAuditLogs: () => ({ data: [] }),
  useDeleteCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCredentialTypes: () => ({ data: [] }),
  useDeleteSystemCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSystemCredentials: () => state.systemList(),
}))
vi.mock('@/lib/hooks/use-credential-test', () => ({
  useStartOAuth2: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/components/credential/credential-test-button', () => ({
  CredentialTestButton: () => <button type="button">시험</button>,
}))
vi.mock('@/components/credential/credential-create-modal', () => ({
  CredentialCreateModal: () => null,
}))
vi.mock('@/app/settings/_components/settings-shell', () => ({
  SettingsShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: state.replace, push: vi.fn() }) }))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: state.user, isPending: false }),
}))
vi.mock('@/lib/api/resource-permissions', () => ({
  RESOURCE_PERMISSION_BATCH: 100,
  resourcePermissionsApi: { list: vi.fn(async () => ({ items: state.permissions })) },
}))

function makeCredential(overrides: Partial<Credential> = {}): Credential {
  return {
    id: 'cred-1',
    user_id: 'user-me',
    definition_key: 'naver_search',
    name: 'Naver key',
    field_keys: ['client_id'],
    is_shared: false,
    status: 'active',
    ...overrides,
  } as Credential
}

const share = { name: 'Naver key 공유 설정 열기' }

function renderDialog(credential: Credential, permissions?: string[]) {
  state.credential = credential
  state.permissions = permissions ? [{ id: credential.id, my_permissions: permissions }] : []
  render(<CredentialDetailDialog credentialId={credential.id} open onOpenChange={vi.fn()} />)
}

describe('credential detail by permission (plan 13.9 자격 증명)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('owner: test, status, re-auth, delete and share', () => {
    renderDialog(makeCredential())
    expect(screen.getByRole('button', { name: '시험' })).toBeVisible()
    expect(screen.getByRole('button', { name: /비활성화/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /삭제/ })).toBeInTheDocument()
    expect(screen.getByRole('button', share)).toBeInTheDocument()
  })

  it('manager of a shared credential: the same actions', async () => {
    renderDialog(makeCredential({ user_id: 'user-other' }), [
      'can_use',
      'can_edit',
      'can_share',
      'can_delete',
    ])
    expect(await screen.findByRole('button', share)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /비활성화/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /삭제/ })).toBeInTheDocument()
  })

  it('consumer of a shared credential: metadata only', async () => {
    renderDialog(makeCredential({ user_id: 'user-other' }), ['can_use'])
    expect(await screen.findByText(/정보만 볼 수 있어요/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: /비활성화/ })).toBeNull())
    expect(screen.queryByRole('button', { name: /삭제/ })).toBeNull()
    expect(screen.queryByRole('button', share)).toBeNull()
  })

  it('no permission (row left out by the server): metadata only', () => {
    renderDialog(makeCredential({ user_id: 'user-other' }))
    expect(screen.getByText(/정보만 볼 수 있어요/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /삭제/ })).toBeNull()
  })
})

describe('system credentials stay operator-only', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends members away without loading system rows', () => {
    state.user = { id: 'user-me', is_super_user: false }
    render(<SystemCredentialsPage />)
    expect(state.replace).toHaveBeenCalledWith('/')
    expect(state.systemList).not.toHaveBeenCalled()
  })

  it('lets operators in', () => {
    state.user = { id: 'op', is_super_user: true }
    state.systemList.mockReturnValue({ data: [], isLoading: false })
    render(<SystemCredentialsPage />)
    expect(state.systemList).toHaveBeenCalled()
    expect(state.replace).not.toHaveBeenCalled()
  })
})
