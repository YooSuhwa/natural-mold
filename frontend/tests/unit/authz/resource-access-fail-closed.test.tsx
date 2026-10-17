import { useResourceAccess, type AccessRow } from '@/lib/authz/use-resource-access'
import { ApiError } from '@/lib/api/errors'
import { PermissionsLoadError } from '@/components/shared/permissions-load-error'
import { render, screen, waitFor } from '../../test-utils'

const api = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/lib/api/resource-permissions', () => ({
  RESOURCE_PERMISSION_BATCH: 100,
  resourcePermissionsApi: api,
}))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: { id: 'me' } }),
}))

const MINE: AccessRow = { id: 'mine', user_id: 'me' }
const THEIRS: AccessRow = { id: 'theirs', user_id: 'other' }
const SKILL: AccessRow = { id: 'skill' } // no owner field

function Probe({ rows }: { rows: AccessRow[] }) {
  const access = useResourceAccess('tool', rows)
  return (
    <>
      <PermissionsLoadError error={access.error} />
      {rows.map((row) => (
        <p key={row.id}>{`${row.id}:${access.can(row, 'can_edit') ? 'edit' : 'read'}`}</p>
      ))}
    </>
  )
}

describe('useResourceAccess fails closed (plan 13.9)', () => {
  beforeEach(() => vi.clearAllMocks())

  it('keeps non-owned rows read-only while permissions load', () => {
    api.list.mockReturnValue(new Promise(() => {}))
    render(<Probe rows={[MINE, THEIRS, SKILL]} />)
    expect(screen.getByText('mine:edit')).toBeInTheDocument()
    expect(screen.getByText('theirs:read')).toBeInTheDocument()
    expect(screen.getByText('skill:read')).toBeInTheDocument()
  })

  it('stays read-only and shows the shared state on 503', async () => {
    api.list.mockRejectedValue(new ApiError(503, 'authz_unavailable', 'x'))
    render(<Probe rows={[MINE, SKILL]} />)
    expect(await screen.findByText('권한을 확인하지 못했어요')).toBeInTheDocument()
    expect(screen.getByText('mine:edit')).toBeInTheDocument()
    expect(screen.getByText('skill:read')).toBeInTheDocument()
  })

  it('follows the answer once it arrives', async () => {
    api.list.mockResolvedValue({
      items: [
        { id: 'theirs', my_permissions: ['can_use', 'can_edit'] },
        { id: 'skill', my_permissions: ['can_use'] },
      ],
    })
    render(<Probe rows={[THEIRS, SKILL]} />)
    await waitFor(() => expect(screen.getByText('theirs:edit')).toBeInTheDocument())
    expect(screen.getByText('skill:read')).toBeInTheDocument()
  })
})
