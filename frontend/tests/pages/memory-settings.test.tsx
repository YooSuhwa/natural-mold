import { render, screen, userEvent, waitFor, within } from '../test-utils'
import MemorySettingsPage from '@/app/settings/memory/page'
import type {
  MemoryPolicy,
  MemoryProfile,
  MemoryRecallPreview,
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

  it('renders the tabs and each tab content', async () => {
    render(<MemorySettingsPage />)

    for (const name of ['개요', '내 프로필', '기억', '회상 미리 보기', '활동', '설정']) {
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
    expect(api.getProfiles).toHaveBeenCalledWith('agent-1')
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
