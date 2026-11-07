import { render, screen, userEvent, waitFor, within } from '../test-utils'
import MemorySettingsPage from '@/app/settings/memory/page'
import type { MemoryPolicy, MemoryRecord, UserMemorySettings } from '@/lib/types'

const api = vi.hoisted(() => ({
  getUserSettings: vi.fn(),
  updateUserSettings: vi.fn(),
  getPolicy: vi.fn(),
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
  revoke: vi.fn(),
  restore: vi.fn(),
  listRevisions: vi.fn(),
  listActivity: vi.fn(),
  countScope: vi.fn(),
  deleteScope: vi.fn(),
}))

vi.mock('@/lib/api/memory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/memory')>()),
  memoryApi: api,
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [{ id: 'agent-1', name: '리서치 에이전트' }], isLoading: false }),
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const SETTINGS: UserMemorySettings = {
  memory_enabled: true,
  memory_read_enabled: true,
  memory_write_policy: 'ask',
  allowed_scopes: 'both',
  trigger_memory_write_policy: 'off',
  approval_mode: 'approve_all',
  approval_axes: null,
  consolidation_enabled: true,
}

const POLICY: MemoryPolicy = {
  tenant_kind: 'company',
  effective_mode: 'approve_all',
  axes: { records: 'approve', profile: 'approve', consolidation: 'approve' },
  allowed_modes: ['off', 'read_only', 'approve_all', 'partial_auto', 'custom'],
  default_mode: 'approve_all',
  consolidation_enabled: true,
  tenant_consolidation_enabled: true,
}

function makeRecord(overrides: Partial<MemoryRecord> = {}): MemoryRecord {
  return {
    id: 'memory-1',
    user_id: 'user-1',
    agent_id: null,
    scope: 'user',
    content: '회의는 오후 3시 이후를 선호합니다.',
    reason: '일정 선호',
    store_path: '/memories/user/profile.md',
    source_conversation_id: null,
    source_message_id: null,
    source_run_id: null,
    status: 'active',
    created_at: '2026-06-03T00:00:00Z',
    updated_at: '2026-06-03T00:00:00Z',
    deleted_at: null,
    kind: 'preference',
    subject: 'self',
    subject_label: null,
    topic: null,
    title: null,
    pinned: false,
    origin: 'user_stated',
    sensitivity: 'normal',
    importance: 1,
    occurred_start: null,
    occurred_end: null,
    valid_until: null,
    approved_by: 'user-1',
    approved_at: '2026-06-03T00:00:00Z',
    approval_channel: 'proposal',
    revoked_at: null,
    revoke_reason: null,
    recall_count: 0,
    flags: {},
    ...overrides,
  }
}

const ACTIVE = makeRecord({ flags: { review_later: true } })
const REVOKED = makeRecord({
  id: 'memory-2',
  content: '보고서는 표로 정리합니다.',
  status: 'revoked',
  revoked_at: '2026-06-05T00:00:00Z',
  origin: 'agent_inferred',
  approval_channel: 'auto_policy',
})

describe('memory settings page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    api.getUserSettings.mockResolvedValue(SETTINGS)
    api.getPolicy.mockResolvedValue(POLICY)
    api.updateUserSettings.mockResolvedValue(SETTINGS)
    api.list.mockImplementation(async (params?: { status?: string }) =>
      params?.status === 'revoked' ? [REVOKED] : [ACTIVE, REVOKED],
    )
    api.listActivity.mockResolvedValue([
      {
        id: 'event-1',
        action: 'memory.record_delete_bulk',
        target_type: 'memory_record',
        target_id: null,
        created_at: '2026-06-06T01:00:00Z',
        metadata: { scope: 'all', deleted: 4, proposals_cleared: 1 },
      },
    ])
    api.listRevisions.mockResolvedValue([
      {
        id: 'rev-1',
        action: 'update',
        actor_user_id: 'user-1',
        run_path: null,
        before_content: '회의는 오전을 선호합니다.',
        after_content: '회의는 오후 3시 이후를 선호합니다.',
        reason: null,
        created_at: '2026-06-04T00:00:00Z',
      },
    ])
    api.revoke.mockResolvedValue({ ...ACTIVE, status: 'revoked' })
    api.restore.mockResolvedValue({ ...REVOKED, status: 'active' })
    api.delete.mockResolvedValue(undefined)
  })

  it('renders the four tabs and each tab content', async () => {
    render(<MemorySettingsPage />)

    for (const name of ['개요', '기억', '활동', '설정']) {
      expect(await screen.findByRole('tab', { name })).toBeInTheDocument()
    }
    expect(await screen.findByTestId('overview-mode-approve_all')).toBeInTheDocument()
    await waitFor(() =>
      expect(within(screen.getByTestId('memory-count-active')).getByText('2')).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByRole('tab', { name: '기억' }))
    const item = await screen.findByTestId('memory-item-memory-1')
    expect(within(item).getByText('회의는 오후 3시 이후를 선호합니다.')).toBeInTheDocument()
    expect(within(item).getByText('선호')).toBeInTheDocument()
    expect(within(item).getByText('직접 말함')).toBeInTheDocument()
    expect(within(item).getByText('승인 후 저장')).toBeInTheDocument()
    expect(within(item).getByTestId('memory-flag-review_later')).toHaveTextContent('나중에 검토')
    expect(api.list).toHaveBeenCalledWith(expect.objectContaining({ status: 'active' }))

    await userEvent.click(screen.getByRole('tab', { name: '활동' }))
    expect(await screen.findByText('기억 전체 삭제')).toBeInTheDocument()
    expect(screen.getByText('삭제 4건, 제안 1건 정리')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: '설정' }))
    expect(await screen.findByLabelText('메모리 활성화')).toBeChecked()
    expect(screen.getByText('사용자 + 에이전트')).toBeInTheDocument()
  })

  it('disables modes the tenant does not allow and saves the chosen mode', async () => {
    render(<MemorySettingsPage />)

    const notAllowed = await screen.findByTestId('overview-mode-auto_notify')
    expect(notAllowed).toBeDisabled()
    expect(notAllowed).toHaveTextContent('관리자가 허용하지 않은 모드입니다.')
    const current = screen.getByTestId('overview-mode-approve_all')
    expect(current).toHaveAttribute('aria-pressed', 'true')
    expect(current).toHaveTextContent('사용 중')
    expect(current).toHaveTextContent('회사 기본값')
    expect(screen.getByTestId('overview-mode-save')).toBeDisabled()

    await userEvent.click(screen.getByTestId('overview-mode-partial_auto'))
    await userEvent.click(screen.getByTestId('overview-mode-save'))
    await waitFor(() =>
      expect(api.updateUserSettings).toHaveBeenCalledWith({ approval_mode: 'partial_auto' }),
    )

    await userEvent.click(screen.getByTestId('overview-mode-custom'))
    expect(screen.getByText('세부 설정')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('overview-mode-save'))
    await waitFor(() =>
      expect(api.updateUserSettings).toHaveBeenLastCalledWith({
        approval_mode: 'custom',
        approval_axes: { records: 'approve', profile: 'approve', consolidation: 'approve' },
      }),
    )
  })

  it('revokes with a reason, restores a revoked memory and deletes after confirming', async () => {
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '기억' }))

    const active = await screen.findByTestId('memory-item-memory-1')
    await userEvent.click(within(active).getByTestId('memory-revoke-button'))
    await userEvent.type(await screen.findByLabelText('철회 사유'), '더는 맞지 않음')
    await userEvent.click(screen.getByTestId('memory-revoke-submit'))
    await waitFor(() => expect(api.revoke).toHaveBeenCalledWith('memory-1', '더는 맞지 않음'))

    const revoked = screen.getByTestId('memory-item-memory-2')
    expect(within(revoked).queryByTestId('memory-revoke-button')).not.toBeInTheDocument()
    await userEvent.click(within(revoked).getByTestId('memory-restore-button'))
    await waitFor(() => expect(api.restore).toHaveBeenCalledWith('memory-2'))

    await userEvent.click(
      within(screen.getByTestId('memory-item-memory-1')).getByTestId('memory-delete-button'),
    )
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/대화 기록과 백업은 지워지지 않습니다/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('memory-1'))
  })

  it('shows the revision history with before and after', async () => {
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '기억' }))
    const item = await screen.findByTestId('memory-item-memory-1')
    await userEvent.click(within(item).getByTestId('memory-history-button'))

    const list = await screen.findByTestId('memory-revisions')
    expect(api.listRevisions).toHaveBeenCalledWith('memory-1')
    expect(within(list).getByText('수정')).toBeInTheDocument()
    expect(within(list).getByText('회의는 오전을 선호합니다.')).toBeInTheDocument()
    expect(within(list).getByText('회의는 오후 3시 이후를 선호합니다.')).toBeInTheDocument()
  })

  it('deletes a scope with the fetched count as confirm_count and shows the receipt', async () => {
    api.countScope.mockResolvedValue({ count: 3 })
    api.deleteScope.mockResolvedValue({
      deleted: 3,
      revisions_removed: 5,
      proposals_cleared: 1,
      derived_marked: 0,
      kept_elsewhere: ['conversation_history', 'backups'],
    })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '설정' }))
    await userEvent.click(await screen.findByTestId('memory-bulk-check'))

    const dialog = await screen.findByRole('alertdialog')
    expect(api.countScope).toHaveBeenCalledWith({ scope: 'all', agentId: null })
    const confirm = within(dialog).getByRole('button', { name: '삭제' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByRole('textbox'), '3')
    await userEvent.click(confirm)

    await waitFor(() =>
      expect(api.deleteScope).toHaveBeenCalledWith({
        scope: 'all',
        agent_id: null,
        confirm_count: 3,
      }),
    )
    const receipt = await screen.findByTestId('memory-deletion-receipt')
    expect(within(receipt).getByText('삭제한 기억 3개')).toBeInTheDocument()
    expect(within(receipt).getByText('지워지지 않고 남는 것: 대화 기록, 백업')).toBeInTheDocument()
  })

  it('shows a dismissible first-use notice before a mode is chosen', async () => {
    api.getUserSettings.mockResolvedValue({ ...SETTINGS, approval_mode: null })
    api.list.mockResolvedValue([])
    render(<MemorySettingsPage />)

    const notice = await screen.findByTestId('memory-first-use-notice')
    expect(screen.getAllByText(/회사 기본값/).length).toBeGreaterThan(0)
    await userEvent.click(within(notice).getByTestId('memory-first-use-choose'))
    expect(screen.getByRole('tab', { name: '설정' })).toHaveAttribute('aria-selected', 'true')

    await userEvent.click(within(notice).getByRole('button', { name: '안내 닫기' }))
    expect(screen.queryByTestId('memory-first-use-notice')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('moldy.memory.firstUseNotice.dismissed')).toBe('1')
  })

  it('creates a memory from the memories tab', async () => {
    api.create.mockResolvedValue(makeRecord({ id: 'memory-3' }))
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '기억' }))

    await userEvent.type(
      await screen.findByLabelText('새 메모리 내용'),
      '문서 초안은 한국어로 먼저 작성합니다.',
    )
    await userEvent.click(screen.getByRole('button', { name: '메모리 추가' }))
    await waitFor(() =>
      expect(api.create).toHaveBeenCalledWith({
        scope: 'user',
        content: '문서 초안은 한국어로 먼저 작성합니다.',
        reason: null,
        agent_id: null,
      }),
    )
  })
})
