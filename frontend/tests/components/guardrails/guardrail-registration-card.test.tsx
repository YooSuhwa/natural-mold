import { toast } from 'sonner'

import { GuardrailRegistrationCard } from '@/features/guardrails/components/guardrail-registration-card'
import { ApiError } from '@/lib/api/client'
import type { RegistrationSettings } from '@/lib/types/guardrails'
import { render, screen, userEvent, waitFor, within } from '../../test-utils'

const registrationApi = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }))

vi.mock('@/lib/api/guardrails', () => ({ registrationApi }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const VERSION = '2026-11-10T00:00:00Z'
const OPEN = { who: 'members', review: 'off', member_sharing: 'shareable' } as const

function settings(overrides: Partial<RegistrationSettings['kinds']> = {}): RegistrationSettings {
  return {
    kinds: {
      mcp_server: { ...OPEN, reviewable: true },
      tool: { ...OPEN, reviewable: true },
      skill: { ...OPEN, reviewable: false },
      agent: { ...OPEN, reviewable: false },
      ...overrides,
    },
    saved: false,
    updated_at: VERSION,
  }
}

async function row(kind: string) {
  await screen.findByText('MCP 서버')
  return within(document.querySelector(`[data-registration-kind="${kind}"]`) as HTMLElement)
}

describe('GuardrailRegistrationCard (회사 등록 규칙)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    registrationApi.get.mockResolvedValue(settings())
    registrationApi.put.mockResolvedValue(settings())
  })

  it('shows a member the rules with nothing to change them with', async () => {
    registrationApi.get.mockResolvedValue(
      settings({
        mcp_server: { who: 'granted', review: 'on', member_sharing: 'private', reviewable: true },
      }),
    )
    render(<GuardrailRegistrationCard tenantId="tenant-1" isAdmin={false} />)

    const server = await row('mcp_server')
    expect(server.getByRole('button', { name: '권한 있는 사람만' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(server.getByRole('button', { name: '확인 후 사용' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(server.getByRole('button', { name: '본인만 사용' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    for (const button of server.getAllByRole('button')) expect(button).toBeDisabled()
    expect(screen.queryByRole('button', { name: '등록 규칙 저장' })).not.toBeInTheDocument()
    expect(screen.getByText(/변경은 회사 관리자가 합니다/)).toBeInTheDocument()
  })

  it('offers the administrator check only for kinds that can be held for review', async () => {
    render(<GuardrailRegistrationCard tenantId="tenant-1" isAdmin />)

    expect((await row('tool')).getByRole('button', { name: '확인 후 사용' })).toBeEnabled()
    expect((await row('skill')).queryByRole('button', { name: '확인 후 사용' })).toBeNull()
    expect((await row('agent')).queryByRole('button', { name: '확인 후 사용' })).toBeNull()
  })

  it('saves every kind with the company version an admin saw', async () => {
    render(<GuardrailRegistrationCard tenantId="tenant-1" isAdmin />)

    const save = await screen.findByRole('button', { name: '등록 규칙 저장' })
    expect(save).toBeDisabled()
    const server = await row('mcp_server')
    await userEvent.click(server.getByRole('button', { name: '권한 있는 사람만' }))
    await userEvent.click(server.getByRole('button', { name: '확인 후 사용' }))
    await userEvent.click((await row('skill')).getByRole('button', { name: '본인만 사용' }))
    await userEvent.click(save)

    await waitFor(() =>
      expect(registrationApi.put).toHaveBeenCalledWith(
        'tenant-1',
        {
          kinds: {
            mcp_server: { who: 'granted', review: 'on', member_sharing: 'shareable' },
            tool: OPEN,
            skill: { who: 'members', review: 'off', member_sharing: 'private' },
            agent: OPEN,
          },
        },
        VERSION,
      ),
    )
    expect(toast.success).toHaveBeenCalledWith('등록 규칙을 저장했습니다.')
  })

  it('keeps the save off again once a change is undone', async () => {
    render(<GuardrailRegistrationCard tenantId="tenant-1" isAdmin />)

    const save = await screen.findByRole('button', { name: '등록 규칙 저장' })
    const tool = await row('tool')
    await userEvent.click(tool.getByRole('button', { name: '권한 있는 사람만' }))
    expect(save).toBeEnabled()
    await userEvent.click(tool.getByRole('button', { name: '모든 구성원' }))
    expect(save).toBeDisabled()
    expect(registrationApi.put).not.toHaveBeenCalled()
  })

  it('tells the admin when the save was refused', async () => {
    registrationApi.put.mockRejectedValue(new ApiError(409, 'stale_version', 'x'))
    render(<GuardrailRegistrationCard tenantId="tenant-1" isAdmin />)

    await userEvent.click((await row('agent')).getByRole('button', { name: '권한 있는 사람만' }))
    await userEvent.click(screen.getByRole('button', { name: '등록 규칙 저장' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(toast.success).not.toHaveBeenCalled()
  })
})
