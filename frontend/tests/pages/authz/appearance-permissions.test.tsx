import AppearanceSettingsPage from '@/app/settings/appearance/page'
import { ApiError } from '@/lib/api/errors'
import { render, screen } from '../../test-utils'

// Plan 13.9 row 15: /settings/appearance is a personal setting, independent of any
// organization role, capability or resource permission.
const apiFetch = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}))
vi.mock('next-themes', () => ({ useTheme: () => ({ theme: 'system', setTheme: vi.fn() }) }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }))

describe('/settings/appearance permissions', () => {
  it('renders fully for a member with no capabilities and no permissions', () => {
    apiFetch.mockRejectedValue(new ApiError(403, 'permission_required', 'x'))
    render(<AppearanceSettingsPage />)
    expect(screen.getByRole('heading', { name: '화면 및 언어' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /다크/ })).toBeEnabled()
    expect(screen.getByRole('button', { name: /한국어/ })).toBeEnabled()
    expect(screen.queryByText('권한이 필요해요')).not.toBeInTheDocument()
    expect(apiFetch).not.toHaveBeenCalled()
  })
})
