import { render, screen, userEvent, waitFor, within } from '../test-utils'
import MemorySettingsPage from '@/app/settings/memory/page'
import { ApiError } from '@/lib/api/client'
import type {
  MemoryVectorModel,
  MemoryVectorStatus,
  MemoryPolicy,
  MemoryProfile,
  MemoryProposal,
  MemoryRecallPreview,
  MemoryTenantPolicy,
  MemoryRecord,
  UserMemorySettings,
} from '@/lib/types'

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
  getProfiles: vi.fn(),
  updateProfile: vi.fn(),
  pin: vi.fn(),
  unpin: vi.fn(),
  previewRecall: vi.fn(),
  getVectorStatus: vi.fn(),
  updateVector: vi.fn(),
  listProposals: vi.fn(),
  approveProposal: vi.fn(),
  rejectProposal: vi.fn(),
  editAndApproveProposal: vi.fn(),
  bulkApproveProposals: vi.fn(),
  getTenantPolicy: vi.fn(),
  updateTenantPolicy: vi.fn(),
}))

const session = vi.hoisted(() => ({ user: { id: 'user-1', is_super_user: false } }))
vi.mock('@/lib/auth/session', () => ({
  useSession: () => ({ data: session.user, isPending: false }),
}))

vi.mock('@/lib/api/memory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/memory')>()),
  memoryApi: api,
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({ data: [{ id: 'agent-1', name: '리서치 에이전트' }], isLoading: false }),
}))

vi.mock('@/lib/hooks/use-triggers', () => ({
  useTriggers: () => ({ data: [{ id: 'trigger-1', name: '월간 매출 보고' }], isLoading: false }),
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

const LARGE: MemoryVectorModel = {
  id: 'embed-large',
  display_name: 'Embedding Large',
  model_name: 'text-embedding-3-large',
  provider: 'openai',
  dimensions: 1024,
  source: 'platform',
}
const SMALL: MemoryVectorModel = {
  ...LARGE,
  id: 'embed-small',
  display_name: 'Embedding Small',
  model_name: 'text-embedding-3-small',
  dimensions: 512,
}

function vectorStatus(overrides: Partial<MemoryVectorStatus> = {}): MemoryVectorStatus {
  return {
    model: null,
    candidates: [LARGE, SMALL],
    total: 12,
    embedded: 0,
    other_vectors: 0,
    job_state: null,
    job_progress: null,
    floor_measured: false,
    can_manage: true,
    ...overrides,
  }
}

const TENANT_VALUES: MemoryTenantPolicy['values'] = {
  default_mode: 'approve_all',
  allowed_modes: ['off', 'read_only', 'approve_all', 'partial_auto', 'auto_notify', 'custom'],
  consolidation_enabled: false,
  channel_personal_memory: false,
  contacts_allowed: true,
}

function tenantPolicy(overrides: Partial<MemoryTenantPolicy> = {}): MemoryTenantPolicy {
  return {
    tenant_kind: 'company',
    values: TENANT_VALUES,
    defaults: TENANT_VALUES,
    can_manage: true,
    ...overrides,
  }
}

function makeProposal(overrides: Partial<MemoryProposal> = {}): MemoryProposal {
  return {
    id: 'proposal-1',
    user_id: 'user-1',
    agent_id: null,
    conversation_id: null,
    source_run_id: null,
    scope: 'user',
    content: '회의는 오후에 잡습니다.',
    reason: null,
    status: 'pending',
    created_at: '2026-06-03T00:00:00Z',
    resolved_at: null,
    kind: 'preference',
    subject: 'self',
    topic: null,
    origin: 'agent_inferred',
    sensitivity: 'normal',
    flags: {},
    action: 'create',
    record_id: null,
    target_record_id: null,
    source_ids: null,
    profile_change: null,
    sources: [],
    bulk_block: null,
    ...overrides,
  }
}

const SOURCE_A = {
  id: 'm-a',
  content: '보고서는 표로 씁니다.',
  status: 'active',
  kind: 'preference',
  provenance: 'confirmed',
}
const SOURCE_B = {
  id: 'm-b',
  content: '표에는 출처를 답니다.',
  status: 'active',
  kind: 'preference',
  provenance: 'agent',
}

const CREATE = makeProposal({ id: 'p-create' })
const MERGE = makeProposal({
  id: 'p-merge',
  action: 'merge',
  origin: 'consolidation',
  flags: { consolidation: true },
  content: '보고서는 표로 씁니다. 표에는 출처를 답니다.',
  reason: '같은 주제의 기억을 합칩니다.',
  source_ids: ['m-a', 'm-b'],
  sources: [SOURCE_A, SOURCE_B],
})
const SUPERSEDE = makeProposal({
  id: 'p-supersede',
  action: 'supersede',
  origin: 'consolidation',
  flags: { consolidation: true, replaces_confirmed: true },
  content: '보고서는 글머리표로 씁니다.',
  target_record_id: 'm-a',
  source_ids: ['m-a'],
  sources: [SOURCE_A],
  bulk_block: 'contradiction',
})
const PROFILE_ADD = makeProposal({
  id: 'p-profile',
  action: 'profile',
  origin: 'consolidation',
  flags: { consolidation: true, needs_review: true },
  content: '보고서는 표로 씁니다.',
  source_ids: ['m-a'],
  sources: [SOURCE_A],
  profile_change: {
    op: 'add',
    kind: 'user_profile',
    agent_id: null,
    text: '보고서는 표로 씁니다.',
    sources: ['m-a'],
  },
  bulk_block: 'marked',
})

/** A select option takes pointer input only once its popup finished opening. */
async function pickOption(option: HTMLElement) {
  await waitFor(() => {
    for (let node: HTMLElement | null = option; node; node = node.parentElement) {
      expect(window.getComputedStyle(node).pointerEvents).not.toBe('none')
    }
  })
  await userEvent.click(option)
}

async function openApprovalsTab() {
  render(<MemorySettingsPage />)
  await userEvent.click(await screen.findByRole('tab', { name: /승인 대기/ }))
}

async function openSettingsTab() {
  render(<MemorySettingsPage />)
  await userEvent.click(await screen.findByRole('tab', { name: '설정' }))
  return screen.findByTestId('memory-vector-card')
}

async function chooseVectorOption(name: string) {
  await userEvent.click(screen.getByTestId('memory-vector-model-trigger'))
  await userEvent.click(await screen.findByRole('option', { name }))
}

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
    api.getVectorStatus.mockResolvedValue(vectorStatus())
    api.listProposals.mockResolvedValue([])
    api.getTenantPolicy.mockResolvedValue(tenantPolicy())
    session.user = { id: 'user-1', is_super_user: false }
  })

  it('renders the tabs and each tab content', async () => {
    render(<MemorySettingsPage />)

    for (const name of [
      '개요',
      '승인 대기',
      '내 프로필',
      '기억',
      '회상 미리 보기',
      '활동',
      '설정',
    ]) {
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

  it('pins an unpinned memory and unpins a pinned one', async () => {
    api.list.mockResolvedValue([ACTIVE, makeRecord({ id: 'memory-4', pinned: true })])
    api.pin.mockResolvedValue(USER_PROFILE)
    api.unpin.mockResolvedValue(USER_PROFILE)
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '기억' }))

    const unpinned = await screen.findByTestId('memory-item-memory-1')
    expect(within(unpinned).queryByTestId('memory-pinned-badge')).not.toBeInTheDocument()
    await userEvent.click(within(unpinned).getByRole('button', { name: '프로필에 올리기' }))
    await waitFor(() => expect(api.pin).toHaveBeenCalledWith('memory-1'))

    const pinned = screen.getByTestId('memory-item-memory-4')
    expect(within(pinned).getByTestId('memory-pinned-badge')).toHaveTextContent('프로필에 올림')
    await userEvent.click(within(pinned).getByRole('button', { name: '프로필에서 내리기' }))
    await waitFor(() => expect(api.unpin).toHaveBeenCalledWith('memory-4'))
    expect(api.pin).toHaveBeenCalledTimes(1)
  })

  it('saves the profile with each kept line id and the edited version', async () => {
    api.getProfiles.mockResolvedValue({ user_profile: USER_PROFILE, work_memo: null })
    api.updateProfile.mockResolvedValue({ ...USER_PROFILE, version: 4 })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '내 프로필' }))

    const editor = await screen.findByTestId('memory-profile-editor-user_profile')
    expect(within(editor).getByText('120/800토큰')).toBeInTheDocument()
    expect(within(editor).getByText('직접 씀')).toBeInTheDocument()
    expect(within(editor).getByText('프로필에 올림')).toBeInTheDocument()
    expect(within(editor).getByText('정리 작업')).toBeInTheDocument()

    const save = within(editor).getByTestId('memory-profile-save-user_profile')
    expect(save).toBeDisabled()
    const second = within(editor).getByLabelText('2번째 줄')
    await userEvent.clear(second)
    await userEvent.type(second, '보고서는 표로')
    await userEvent.click(within(editor).getByLabelText('3번째 줄 지우기'))
    await userEvent.click(within(editor).getByTestId('memory-profile-add-line'))
    await userEvent.type(within(editor).getByLabelText('3번째 줄'), '새 줄')
    await userEvent.click(save)

    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith('user_profile', {
        agent_id: null,
        lines: [
          { id: 'line-1', text: '이름은 지수입니다.' },
          { id: 'line-2', text: '보고서는 표로' },
          { id: null, text: '새 줄' },
        ],
        version: 3,
      }),
    )
  })

  it('marks a profile line whose source memory is gone and explains it', async () => {
    api.getProfiles.mockResolvedValue({ user_profile: USER_PROFILE, work_memo: null })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '내 프로필' }))

    const editor = await screen.findByTestId('memory-profile-editor-user_profile')
    const lines = within(editor).getAllByTestId('memory-profile-line')
    expect(lines[2]).toHaveAttribute('data-unreadable', 'true')
    expect(within(lines[2]).getByTestId('memory-profile-line-unreadable')).toHaveTextContent(
      '실행에서 빠짐',
    )
    expect(lines[0]).not.toHaveAttribute('data-unreadable')
    expect(within(editor).getByTestId('memory-profile-unreadable-notice')).toHaveTextContent(
      /철회되거나 삭제되어 실행에서 빠집니다/,
    )
  })

  it('loads and saves an agent work memo with its agent id', async () => {
    const workMemo: MemoryProfile = {
      kind: 'work_memo',
      agent_id: 'agent-1',
      lines: [],
      tokens: 0,
      budget_tokens: 800,
      version: 0,
    }
    api.getProfiles.mockImplementation(async (agentId?: string | null) => ({
      user_profile: USER_PROFILE,
      work_memo: agentId ? workMemo : null,
    }))
    api.updateProfile.mockResolvedValue({ ...workMemo, version: 1 })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '내 프로필' }))

    await userEvent.click(await screen.findByTestId('memory-profile-agent-trigger'))
    await userEvent.click(await screen.findByTestId('memory-profile-agent-option-agent-1'))
    const editor = await screen.findByTestId('memory-profile-editor-work_memo')
    expect(api.getProfiles).toHaveBeenCalledWith('agent-1', null)
    await userEvent.click(within(editor).getByTestId('memory-profile-add-line'))
    await userEvent.type(within(editor).getByLabelText('1번째 줄'), '주간 보고는 금요일')
    await userEvent.click(within(editor).getByTestId('memory-profile-save-work_memo'))
    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith('work_memo', {
        agent_id: 'agent-1',
        lines: [{ id: null, text: '주간 보고는 금요일' }],
        version: 0,
      }),
    )
  })

  it('edits a schedule memo by section and saves it with its schedule id', async () => {
    const memo: MemoryProfile = {
      kind: 'trigger_memo',
      agent_id: 'agent-1',
      trigger_id: 'trigger-1',
      lines: [
        {
          id: 'l-1',
          text: '3월분 보고 완료',
          origin: 'schedule',
          sources: [],
          readable: true,
          section: 'recent',
        },
      ],
      tokens: 6,
      budget_tokens: 1500,
      version: 2,
    }
    api.getProfiles.mockImplementation(
      async (agentId?: string | null, triggerId?: string | null) => ({
        user_profile: USER_PROFILE,
        work_memo: null,
        trigger_memo: agentId && triggerId ? memo : null,
      }),
    )
    api.updateProfile.mockResolvedValue({ ...memo, version: 3 })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '내 프로필' }))
    await userEvent.click(await screen.findByTestId('memory-profile-agent-trigger'))
    await pickOption(await screen.findByTestId('memory-profile-agent-option-agent-1'))
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
    await userEvent.click(await screen.findByTestId('memory-profile-trigger-trigger'))
    await pickOption(await screen.findByTestId('memory-profile-trigger-option-trigger-1'))
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())

    const editor = await screen.findByTestId('memory-profile-editor-trigger_memo')
    expect(api.getProfiles).toHaveBeenCalledWith('agent-1', 'trigger-1')
    expect(within(editor).getByText('예약 실행')).toBeInTheDocument()
    expect(within(editor).getByTestId('memory-profile-tokens-trigger_memo')).toHaveTextContent(
      '6/1500',
    )
    await userEvent.click(within(editor).getByTestId('memory-profile-add-line'))
    await userEvent.type(within(editor).getByLabelText('2번째 줄'), '결과는 #sales로 보낸다')
    await userEvent.click(within(editor).getByLabelText('2번째 줄의 절'))
    await pickOption(await screen.findByRole('option', { name: '실전 요령' }))
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
    await userEvent.click(within(editor).getByTestId('memory-profile-save-trigger_memo'))
    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith('trigger_memo', {
        agent_id: 'agent-1',
        trigger_id: 'trigger-1',
        lines: [
          { id: 'l-1', text: '3월분 보고 완료', section: 'recent' },
          { id: null, text: '결과는 #sales로 보낸다', section: 'tips' },
        ],
        version: 2,
      }),
    )
  })

  it('shows included and left-out recall candidates with their reasons and scores', async () => {
    api.previewRecall.mockResolvedValue(FRESH_PREVIEW)
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '회상 미리 보기' }))

    await userEvent.type(await screen.findByTestId('memory-preview-message'), '출장 일정 정리')
    await userEvent.click(screen.getByTestId('memory-preview-submit'))
    await waitFor(() =>
      expect(api.previewRecall).toHaveBeenCalledWith({
        message: '출장 일정 정리',
        agent_id: null,
        previous: [],
      }),
    )

    const result = await screen.findByTestId('memory-preview-result')
    expect(within(result).getByTestId('memory-preview-mode')).toHaveTextContent('새로 찾음')
    expect(within(result).getByTestId('memory-preview-analyzer')).toHaveTextContent('Nori')
    expect(within(result).getByTestId('memory-preview-vector')).toHaveTextContent('기준 미측정')
    expect(within(result).getByText('이름은 지수입니다.')).toBeInTheDocument()

    const included = within(result).getByTestId('memory-preview-item-m-in')
    expect(included).toHaveAttribute('data-included', 'true')
    expect(included).toHaveTextContent('들어감')
    expect(included).toHaveTextContent('키워드 0.82')
    expect(included).toHaveTextContent('코사인 0.61')
    expect(included).toHaveTextContent('최종 0.74')
    const budget = within(result).getByTestId('memory-preview-item-m-budget')
    expect(budget).toHaveAttribute('data-included', 'false')
    expect(budget).toHaveTextContent('빠짐: 예산 초과')
    expect(within(result).getByTestId('memory-preview-item-m-cap')).toHaveTextContent(
      '빠짐: 범위 상한',
    )
    expect(within(result).getByTestId('memory-preview-item-m-below')).toHaveTextContent(
      '빠짐: 기준 미달',
    )
  })

  it.each([
    ['skipped', '찾지 않음', /인사나 짧은 맞장구/],
    ['reference', '직전 회상 재사용', /직전 턴에 떠올린 기억을 그대로 씁니다/],
  ] as const)('explains the %s recall mode', async (mode, label, explanation) => {
    api.previewRecall.mockResolvedValue({
      ...FRESH_PREVIEW,
      mode,
      analyzer: mode === 'skipped' ? '' : 'suffix',
      vector: 'off',
      items: [],
    })
    render(<MemorySettingsPage />)
    await userEvent.click(await screen.findByRole('tab', { name: '회상 미리 보기' }))
    await userEvent.type(await screen.findByTestId('memory-preview-message'), '고마워')
    await userEvent.click(screen.getByTestId('memory-preview-submit'))

    const result = await screen.findByTestId('memory-preview-result')
    expect(result).toHaveAttribute('data-mode', mode)
    expect(within(result).getByTestId('memory-preview-mode')).toHaveTextContent(label)
    expect(screen.getByText(explanation)).toBeInTheDocument()
    expect(within(result).getByTestId('memory-preview-vector')).toHaveTextContent('꺼짐')
    expect(within(result).getByTestId('memory-preview-analyzer')).toHaveTextContent(
      mode === 'skipped' ? '쓰지 않음' : '접미사 목록',
    )
    expect(within(result).queryByText('후보 기억')).not.toBeInTheDocument()
  })

  describe('memory vector model card (Q14)', () => {
    it('cannot be turned on without a registered embedding model', async () => {
      session.user = { id: 'op', is_super_user: true }
      api.getVectorStatus.mockResolvedValue(vectorStatus({ candidates: [] }))
      await openSettingsTab()

      expect(screen.getByTestId('memory-vector-state')).toHaveTextContent('꺼져 있습니다')
      expect(screen.getByTestId('memory-vector-model-trigger')).toBeDisabled()
      const note = screen.getByTestId('memory-vector-no-models')
      expect(note).toHaveTextContent('등록된 임베딩 모델이 없어 켤 수 없습니다.')
      expect(within(note).getByRole('link')).toHaveAttribute('href', '/settings/models')
    })

    it('is read-only for someone who cannot manage the company', async () => {
      api.getVectorStatus.mockResolvedValue(
        vectorStatus({
          model: LARGE,
          embedded: 7,
          can_manage: false,
          floor_measured: true,
          job_state: 'running',
          job_progress: 40,
          other_vectors: 3,
        }),
      )
      await openSettingsTab()

      expect(screen.getByTestId('memory-vector-state')).toHaveTextContent(
        '현재 임베딩: Embedding Large (1024차원) — 7/12개 완료',
      )
      expect(screen.getByTestId('memory-vector-job')).toHaveTextContent(
        '다시 임베딩 중 40%. 끝날 때까지 키워드로만 찾습니다.',
      )
      expect(screen.getByTestId('memory-vector-other')).toHaveTextContent('3개')
      expect(screen.queryByTestId('memory-vector-unmeasured')).not.toBeInTheDocument()
      expect(screen.getByTestId('memory-vector-model-trigger')).toBeDisabled()
      expect(screen.getByTestId('memory-vector-read-only')).toHaveTextContent(
        '회사 관리자만 바꿀 수 있습니다.',
      )
      expect(screen.queryByTestId('memory-vector-no-models')).not.toBeInTheDocument()
    })

    it('confirms how many memories are embedded again and sends that count', async () => {
      api.getVectorStatus.mockResolvedValue(vectorStatus({ model: LARGE, embedded: 12 }))
      api.updateVector.mockResolvedValue(vectorStatus({ model: SMALL, job_state: 'queued' }))
      await openSettingsTab()
      expect(screen.getByTestId('memory-vector-unmeasured')).toHaveTextContent(
        '기준 미측정: 평가 전까지 키워드로만 찾습니다.',
      )

      await chooseVectorOption('Embedding Small (512차원)')
      // Vectors of a model other than the current one are not counted: all 12 again.
      expect(await screen.findByTestId('memory-vector-confirm')).toHaveTextContent(
        '기억 12개를 다시 임베딩합니다. 끝날 때까지 키워드로만 찾습니다. 바꿀까요?',
      )
      await userEvent.click(screen.getByRole('button', { name: '바꾸기' }))
      await waitFor(() =>
        expect(api.updateVector).toHaveBeenCalledWith({
          model_id: 'embed-small',
          confirm_count: 12,
        }),
      )
      await waitFor(() =>
        expect(screen.queryByTestId('memory-vector-confirm')).not.toBeInTheDocument(),
      )
    })

    it('re-reads and asks again with the server count when the count changed', async () => {
      api.updateVector
        .mockRejectedValueOnce(
          new ApiError(
            422,
            'MEMORY_VECTOR_COUNT_CHANGED',
            '다시 임베딩할 기억 수가 바뀌었습니다(15개). 새로 확인해 주세요',
          ),
        )
        .mockResolvedValueOnce(vectorStatus({ model: LARGE, total: 15, job_state: 'queued' }))
      await openSettingsTab()

      await chooseVectorOption('Embedding Large (1024차원)')
      expect(await screen.findByTestId('memory-vector-confirm')).toHaveTextContent('기억 12개')
      const reads = api.getVectorStatus.mock.calls.length
      api.getVectorStatus.mockResolvedValue(vectorStatus({ total: 15 }))
      await userEvent.click(screen.getByRole('button', { name: '바꾸기' }))

      await waitFor(() =>
        expect(screen.getByTestId('memory-vector-confirm')).toHaveTextContent('기억 15개'),
      )
      expect(screen.getByRole('alert')).toHaveTextContent('그사이 기억 수가 바뀌었습니다')
      expect(api.getVectorStatus.mock.calls.length).toBeGreaterThan(reads)
      await userEvent.click(screen.getByRole('button', { name: '바꾸기' }))
      await waitFor(() =>
        expect(api.updateVector).toHaveBeenLastCalledWith({
          model_id: 'embed-large',
          confirm_count: 15,
        }),
      )
    })

    it('turns off with confirm_count 0 and asks whether to drop the vectors', async () => {
      api.getVectorStatus.mockResolvedValue(
        vectorStatus({ model: LARGE, embedded: 12, floor_measured: true }),
      )
      api.updateVector.mockResolvedValue(vectorStatus())
      await openSettingsTab()

      await chooseVectorOption('끔')
      const dialog = await screen.findByTestId('memory-vector-off')
      const drop = within(dialog).getByRole('checkbox', { name: '저장된 벡터도 지우기' })
      expect(drop).not.toBeChecked()
      await userEvent.click(drop)
      await userEvent.click(screen.getByRole('button', { name: '끄기' }))
      await waitFor(() =>
        expect(api.updateVector).toHaveBeenCalledWith({
          model_id: null,
          confirm_count: 0,
          drop_vectors: true,
        }),
      )
    })
  })

  describe('approval queue', () => {
    beforeEach(() => {
      api.listProposals.mockResolvedValue([CREATE, MERGE, SUPERSEDE, PROFILE_ADD])
    })

    it('shows the pending count and each change as before and after', async () => {
      await openApprovalsTab()
      expect(api.listProposals).toHaveBeenCalledWith('pending')
      expect(screen.getByTestId('memory-approvals-count')).toHaveTextContent('4')

      const merge = await screen.findByTestId('memory-proposal-p-merge')
      expect(within(merge).getByText('합치기')).toBeInTheDocument()
      const mergeBefore = within(merge).getByTestId('memory-proposal-before-p-merge')
      expect(within(mergeBefore).getByText('보고서는 표로 씁니다.')).toBeInTheDocument()
      expect(within(mergeBefore).getByText('표에는 출처를 답니다.')).toBeInTheDocument()
      expect(within(merge).getByTestId('memory-proposal-after-p-merge')).toHaveTextContent(
        '보고서는 표로 씁니다. 표에는 출처를 답니다.',
      )
      expect(within(merge).getByText('이유: 같은 주제의 기억을 합칩니다.')).toBeInTheDocument()
      expect(within(merge).getByText('정리로 만듦')).toBeInTheDocument()

      const supersede = screen.getByTestId('memory-proposal-p-supersede')
      const old = within(supersede).getByText('보고서는 표로 씁니다.')
      expect(old.tagName).toBe('DEL')
      expect(within(supersede).getByTestId('memory-proposal-after-p-supersede')).toHaveTextContent(
        '보고서는 글머리표로 씁니다.',
      )
      expect(
        within(supersede).getByTestId('memory-proposal-flag-replaces_confirmed'),
      ).toHaveTextContent('확인한 기억을 바꿈')

      const profile = screen.getByTestId('memory-proposal-p-profile')
      expect(within(profile).getByText('프로필 줄 추가')).toBeInTheDocument()
      const after = within(profile).getByTestId('memory-proposal-after-p-profile')
      expect(after).toHaveTextContent('내 프로필에 이 줄을 더합니다.')
      expect(within(after).getByText('근거 기억')).toBeInTheDocument()
      expect(within(after).getByText('사람이 확인함')).toBeInTheDocument()
      expect(within(profile).getByTestId('memory-proposal-flag-needs_review')).toHaveTextContent(
        '확인 필요',
      )
    })

    it('shows a schedule memo proposal as the new recent status and tips', async () => {
      api.listProposals.mockResolvedValue([
        makeProposal({
          id: 'p-trigger',
          action: 'profile',
          origin: 'agent_inferred',
          flags: { trigger_memo: true },
          content: '4월 자료가 아직 없다',
          source_ids: [],
          sources: [],
          profile_change: {
            op: 'replace_section',
            kind: 'trigger_memo',
            agent_id: 'agent-1',
            trigger_id: 'trigger-1',
            recent: ['4월 자료가 아직 없다'],
            tips: ['자료가 없으면 기다린다'],
          },
          bulk_block: null,
        }),
      ])
      await openApprovalsTab()
      const card = await screen.findByTestId('memory-proposal-p-trigger')
      expect(within(card).getByText('예약 메모 최근 상태')).toBeInTheDocument()
      expect(within(card).getByTestId('memory-proposal-before-p-trigger')).toHaveTextContent(
        '최근 상태’ 절 전체가 이 줄들로 바뀝니다',
      )
      const after = within(card).getByTestId('memory-proposal-trigger-memo-p-trigger')
      expect(within(after).getByText('4월 자료가 아직 없다')).toBeInTheDocument()
      expect(within(after).getByText('자료가 없으면 기다린다')).toBeInTheDocument()
      expect(within(card).queryByTestId('memory-proposal-flag-trigger_memo')).toBeNull()
    })

    it('offers editing only for new memories', async () => {
      await openApprovalsTab()
      await screen.findByTestId('memory-proposal-p-merge')
      expect(screen.getByTestId('memory-proposal-edit-p-create')).toBeInTheDocument()
      for (const id of ['p-merge', 'p-supersede', 'p-profile']) {
        expect(screen.queryByTestId(`memory-proposal-edit-${id}`)).not.toBeInTheDocument()
      }
    })

    it('bulk-approves only eligible proposals and reports what was skipped', async () => {
      api.bulkApproveProposals.mockResolvedValue({
        approved: ['p-create'],
        skipped: [{ id: 'p-merge', reason: 'memory_proposal_stale' }],
      })
      await openApprovalsTab()
      await screen.findByTestId('memory-proposal-p-merge')
      expect(screen.getByTestId('memory-approvals-excluded')).toHaveTextContent(
        '2개는 일괄 승인에서 빠집니다.',
      )
      expect(screen.getByTestId('memory-proposal-select-p-supersede')).toHaveAttribute(
        'aria-disabled',
        'true',
      )
      expect(screen.getByTestId('memory-proposal-blocked-p-supersede')).toHaveTextContent(
        '일괄 승인 제외: 모순·대체 판정이라 하나씩 결정합니다',
      )
      expect(screen.getByTestId('memory-proposal-blocked-p-profile')).toHaveTextContent(
        '표시가 붙어 하나씩 결정합니다',
      )

      await userEvent.click(screen.getByTestId('memory-approvals-select-all'))
      const submit = screen.getByTestId('memory-approvals-bulk-submit')
      expect(submit).toHaveTextContent('선택 항목 일괄 승인 (2)')
      await userEvent.click(submit)

      await waitFor(() => expect(api.bulkApproveProposals).toHaveBeenCalledTimes(1))
      expect(api.bulkApproveProposals.mock.calls[0][0]).toEqual(['p-create', 'p-merge'])
      const skipped = await screen.findByTestId('memory-approvals-skipped')
      expect(skipped).toHaveTextContent('건너뛴 항목 1개')
      expect(skipped).toHaveTextContent('근거 기억이 바뀌어 만료되었습니다')
    })

    it('shows the server message and refetches when a proposal went stale', async () => {
      const { toast } = await import('sonner')
      api.approveProposal.mockRejectedValue(
        new ApiError(
          422,
          'MEMORY_PROPOSAL_STALE',
          '근거 기억이 바뀌어 이 제안은 더 쓸 수 없습니다',
        ),
      )
      await openApprovalsTab()
      await screen.findByTestId('memory-proposal-p-merge')
      const calls = api.listProposals.mock.calls.length

      await userEvent.click(screen.getByTestId('memory-proposal-approve-p-merge'))
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith('근거 기억이 바뀌어 이 제안은 더 쓸 수 없습니다'),
      )
      await waitFor(() => expect(api.listProposals.mock.calls.length).toBeGreaterThan(calls))
    })

    it('shows an empty state when nothing waits', async () => {
      api.listProposals.mockResolvedValue([])
      await openApprovalsTab()
      expect(await screen.findByText('승인을 기다리는 제안이 없습니다')).toBeInTheDocument()
      expect(screen.queryByTestId('memory-approvals-count')).not.toBeInTheDocument()
    })
  })

  describe('tenant memory policy', () => {
    it('is read-only with defaults noted when the person cannot manage it', async () => {
      api.getTenantPolicy.mockResolvedValue(
        tenantPolicy({
          can_manage: false,
          values: { ...TENANT_VALUES, consolidation_enabled: true },
        }),
      )
      await openSettingsTab()
      const card = await screen.findByTestId('tenant-policy-read-only')
      expect(within(card).getByText('회사 관리자가 바꿉니다.')).toBeInTheDocument()
      expect(screen.getByText('회사 메모리 정책')).toBeInTheDocument()
      expect(
        within(card).getByTestId('tenant-policy-default-consolidation_enabled'),
      ).toHaveTextContent('기본값: 끔')
      expect(
        within(card).queryByTestId('tenant-policy-default-default_mode'),
      ).not.toBeInTheDocument()
      expect(screen.queryByTestId('tenant-policy-save')).not.toBeInTheDocument()
    })

    it('limits the default mode to allowed modes and saves the whole policy', async () => {
      api.getTenantPolicy.mockResolvedValue(tenantPolicy({ tenant_kind: 'personal' }))
      api.updateTenantPolicy.mockImplementation(async (values: MemoryTenantPolicy['values']) =>
        tenantPolicy({ tenant_kind: 'personal', values }),
      )
      await openSettingsTab()
      await screen.findByTestId('tenant-policy-form')
      expect(screen.getByText('개인 테넌트 메모리 정책')).toBeInTheDocument()
      expect(screen.getByTestId('tenant-policy-save')).toBeDisabled()

      // Dropping the current default moves the default to an allowed mode.
      await userEvent.click(screen.getByTestId('tenant-policy-allowed-approve_all'))
      await userEvent.click(screen.getByTestId('tenant-policy-allowed-auto_notify'))
      await userEvent.click(screen.getByTestId('tenant-policy-default-mode-trigger'))
      const options = (await screen.findAllByRole('option')).map((option) => option.textContent)
      expect(options).toEqual(['메모리 끄기', '읽기만', '일부 자동', '직접 고르기'])
      await userEvent.click(screen.getByRole('option', { name: '일부 자동' }))
      expect(screen.getByTestId('tenant-policy-default-default_mode')).toHaveTextContent(
        '기본값: 모두 승인',
      )
      await userEvent.click(screen.getByTestId('tenant-policy-consolidation_enabled'))

      await userEvent.click(screen.getByTestId('tenant-policy-save'))
      await waitFor(() =>
        expect(api.updateTenantPolicy).toHaveBeenCalledWith({
          default_mode: 'partial_auto',
          allowed_modes: ['off', 'read_only', 'partial_auto', 'custom'],
          consolidation_enabled: true,
          channel_personal_memory: false,
          contacts_allowed: true,
        }),
      )
    })
  })
})

const USER_PROFILE: MemoryProfile = {
  kind: 'user_profile',
  agent_id: null,
  lines: [
    { id: 'line-1', text: '이름은 지수입니다.', origin: 'user', sources: [], readable: true },
    {
      id: 'line-2',
      text: '회의는 오후 3시 이후를 선호합니다.',
      origin: 'pinned',
      sources: ['memory-1'],
      readable: true,
    },
    {
      id: 'line-3',
      text: '분기 보고서는 표로 정리합니다.',
      origin: 'consolidation',
      sources: ['memory-gone'],
      readable: false,
    },
  ],
  tokens: 120,
  budget_tokens: 800,
  version: 3,
}

const FRESH_PREVIEW: MemoryRecallPreview = {
  mode: 'fresh',
  analyzer: 'nori',
  vector: 'unmeasured',
  profile_lines: ['이름은 지수입니다.'],
  items: [
    {
      id: 'm-in',
      scope: 'user',
      kind: 'fact',
      content: '출장은 KTX를 탑니다.',
      keyword: 0.82,
      cosine: 0.61,
      search: 0.7,
      final: 0.74,
      included: true,
      left_out: null,
    },
    {
      id: 'm-budget',
      scope: 'user',
      kind: 'fact',
      content: '호텔은 역 근처를 고릅니다.',
      keyword: 0.5,
      cosine: null,
      search: 0.5,
      final: 0.5,
      included: false,
      left_out: 'budget',
    },
    {
      id: 'm-cap',
      scope: 'agent',
      kind: 'preference',
      content: '일정표는 표로 만듭니다.',
      keyword: 0.4,
      cosine: null,
      search: 0.4,
      final: 0.4,
      included: false,
      left_out: 'scope_cap',
    },
    {
      id: 'm-below',
      scope: 'user',
      kind: 'fact',
      content: '커피는 라떼를 마십니다.',
      keyword: 0.1,
      cosine: null,
      search: 0.1,
      final: 0.1,
      included: false,
      left_out: null,
    },
  ],
  tokens: 40,
  budget_tokens: 400,
}
