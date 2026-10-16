import { render, screen } from '../../test-utils'
import { CredentialDetailDialog } from '@/components/credential/credential-detail-dialog'
import type { Credential } from '@/lib/types/credential'

const { credential } = vi.hoisted(() => ({ credential: { current: null as unknown } }))

vi.mock('@/lib/hooks/use-credentials', () => ({
  useCredential: () => ({ data: credential.current, isLoading: false }),
  useCredentialAuditLogs: () => ({ data: [] }),
  useDeleteCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-credential-test', () => ({
  useStartOAuth2: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTestCredential: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/components/credential/credential-test-button', () => ({
  CredentialTestButton: () => null,
}))
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'user-me' } }) }))

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

describe('CredentialDetailDialog sharing entry', () => {
  it('offers sharing to the owner of a non-personal credential', () => {
    credential.current = makeCredential()
    render(<CredentialDetailDialog credentialId="cred-1" open onOpenChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Naver key 공유 설정 열기' })).toBeInTheDocument()
  })

  it('hides sharing for personal account credentials (10.4, E-05)', () => {
    credential.current = makeCredential({ personal: true, definition_key: 'srt_account' })
    render(<CredentialDetailDialog credentialId="cred-1" open onOpenChange={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /공유 설정 열기/ })).not.toBeInTheDocument()
  })

  it('hides sharing for credentials owned by someone else', () => {
    credential.current = makeCredential({ user_id: 'user-other' })
    render(<CredentialDetailDialog credentialId="cred-1" open onOpenChange={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /공유 설정 열기/ })).not.toBeInTheDocument()
  })
})
