import { render, screen, userEvent, within } from '../test-utils'
// Phase 2: page.tsx는 async 서버 redirect 래퍼 — UI는 클라이언트 컴포넌트를 직접 렌더.
import { SkillsPageClient } from '@/app/skills/_components/skills-page-client'
import type { Skill } from '@/lib/types/skill'

const mockUseSkills = vi.fn()
const mockCreateDialog = vi.fn()
const mockDeleteSkill = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: (...args: unknown[]) => mockUseSkills(...args),
  useDeleteSkill: () => ({ mutateAsync: mockDeleteSkill, isPending: false }),
}))

vi.mock('@/components/skill/skill-create-dialog', () => ({
  SkillCreateDialog: (props: { readonly open: boolean; readonly initialTab?: string }) => {
    mockCreateDialog(props)
    return props.open ? <div data-testid="skill-create-dialog">{props.initialTab}</div> : null
  },
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgents: () => ({
    data: [
      {
        id: 'agent-1',
        name: '회의 비서',
        skills: [{ id: 'skill-1', name: 'Korea Weather' }],
      },
    ],
  }),
}))

vi.mock('@/components/marketplace/publish-wizard', () => ({
  PublishWizard: () => null,
}))

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}))

const skill: Skill = {
  id: 'skill-1',
  name: 'Korea Weather',
  slug: 'korea-weather',
  description: '한국 날씨를 조회합니다.',
  kind: 'package',
  version: '0.1.0',
  storage_path: null,
  content_hash: null,
  size_bytes: 1200,
  used_by_count: 2,
  package_metadata: null,
  health: {
    state: 'ready',
    label: '검증됨',
    reason: 'Latest evaluation passed for the current skill.',
    severity: 'success',
  },
  latest_evaluation_summary: {
    status: 'completed',
    latest_run_id: 'run-1',
    evaluation_set_id: 'set-1',
    pass_rate: 0.92,
    skill_content_hash: 'hash-1',
    created_at: '2026-06-01T00:00:00Z',
    completed_at: '2026-06-01T00:01:00Z',
  },
  last_modified_at: '2026-05-01T00:00:00Z',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

function buildSkill(overrides: Partial<Skill>): Skill {
  return {
    ...skill,
    ...overrides,
  }
}

function publishedSummary(id: string): NonNullable<Skill['publication_summary']> {
  return {
    state: 'published_private',
    item_id: id,
    visibility: 'private',
    status: 'published',
    is_listed: false,
    latest_version_id: `${id}-version`,
    version_number: 1,
    shared_user_count: 0,
  }
}

describe('SkillsPage', () => {
  beforeEach(() => {
    mockCreateDialog.mockClear()
    mockUseSkills.mockReturnValue({ data: [skill], isLoading: false })
  })

  it('스튜디오 목록을 표(DataTable)로 렌더한다 — Phase 2', () => {
    render(<SkillsPageClient />)

    expect(screen.getByRole('tab', { name: '전체 1개' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('스킬 검색')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /스킬/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /에이전트/ })).toBeInTheDocument()
    expect(screen.getByText('Korea Weather')).toBeInTheDocument()
    expect(screen.getByText(/korea-weather · v0\.1\.0/)).toBeInTheDocument()
    // 연결 카운트 실데이터 (M1)
    expect(screen.getByText('2개 에이전트')).toBeInTheDocument()
  })

  it('표 행에 상태·평가 요약 배지를 보여준다', () => {
    render(<SkillsPageClient />)

    expect(screen.getByText('검증됨')).toBeInTheDocument()
    expect(screen.getByText('평가 92%')).toBeInTheDocument()
  })

  it("'선택 해제'가 controlled 선택(rowSelection+selected)을 함께 리셋한다", async () => {
    const user = userEvent.setup()
    render(<SkillsPageClient />)

    // 행 단위 체크박스 경로(프로젝트 규칙 — 헤더 전체선택만 쓰면 행 클릭
    // 전파 클래스를 못 잡는다).
    await user.click(screen.getByRole('checkbox', { name: '행 선택' }))
    expect(screen.getByTestId('skill-bulk-bar')).toHaveTextContent('1개 선택됨')

    await user.click(screen.getByRole('button', { name: '선택 해제' }))

    expect(screen.queryByTestId('skill-bulk-bar')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '행 선택' })).not.toBeChecked()
  })

  it('행 선택 시 벌크 바가 뜨고 일괄 삭제 확인에 이름을 열거한다', async () => {
    const user = userEvent.setup()
    mockDeleteSkill.mockResolvedValue(undefined)
    render(<SkillsPageClient />)

    expect(screen.queryByTestId('skill-bulk-bar')).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '모든 행 선택' }))

    expect(screen.getByTestId('skill-bulk-bar')).toHaveTextContent('1개 선택됨')

    await user.click(within(screen.getByTestId('skill-bulk-bar')).getByRole('button', { name: '삭제' }))

    // 확인 다이얼로그 — 검색으로 숨은 선택 행 방어를 위해 대상 이름을 명시한다.
    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toHaveTextContent('Korea Weather')
    expect(dialog).toHaveTextContent('연결된 에이전트 2개')
    // AD-4.1 — 영향받는 에이전트 이름 역도출 표시.
    expect(dialog).toHaveTextContent('영향받는 에이전트: 회의 비서')

    await user.click(within(dialog).getByRole('button', { name: '삭제' }))

    expect(mockDeleteSkill).toHaveBeenCalledWith('skill-1')
  })

  it('벌크 삭제의 404는 멱등 성공 — 실패 토스트를 오발하지 않는다 (R5 규칙 ④)', async () => {
    const { ApiError } = await import('@/lib/api/errors')
    const user = userEvent.setup()
    mockToastSuccess.mockClear()
    mockToastError.mockClear()
    // 다른 탭에서 이미 삭제된 대상 — 백엔드는 404를 돌려준다.
    mockDeleteSkill.mockRejectedValue(new ApiError(404, 'SKILL_NOT_FOUND', 'not found'))
    render(<SkillsPageClient />)

    await user.click(screen.getByRole('checkbox', { name: '모든 행 선택' }))
    await user.click(
      within(screen.getByTestId('skill-bulk-bar')).getByRole('button', { name: '삭제' }),
    )
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '삭제' }))

    expect(mockToastError).not.toHaveBeenCalled()
    expect(mockToastSuccess).toHaveBeenCalled()
  })

  it('filters skills from the shared tab row', async () => {
    const user = userEvent.setup()
    render(<SkillsPageClient />)

    await user.click(screen.getByRole('tab', { name: /패키지/ }))

    expect(mockUseSkills).toHaveBeenLastCalledWith({ kind: 'package' })
    expect(screen.getByRole('tab', { name: '패키지 1개' })).toHaveAttribute('aria-selected', 'true')
  })

  it('filters skills from compact state chips', async () => {
    const user = userEvent.setup()
    mockUseSkills.mockReturnValue({
      data: [
        buildSkill({
          id: 'skill-needs-credentials',
          name: 'Credential Setup',
          health: {
            state: 'needs_credentials',
            label: '자격증명 필요',
            reason: '필수 자격증명이 없습니다.',
            severity: 'warning',
          },
          publication_summary: publishedSummary('item-credentials'),
        }),
        buildSkill({
          id: 'skill-needs-rerun',
          name: 'Rerun Needed',
          health: {
            state: 'needs_rerun',
            label: '재평가 필요',
            reason: '콘텐츠가 바뀌었습니다.',
            severity: 'warning',
          },
          publication_summary: publishedSummary('item-rerun'),
        }),
        buildSkill({
          id: 'skill-failed',
          name: 'Failed Eval',
          health: {
            state: 'evaluation_failed',
            label: '평가 실패',
            reason: '마지막 평가가 실패했습니다.',
            severity: 'error',
          },
          publication_summary: publishedSummary('item-failed'),
        }),
        buildSkill({
          id: 'skill-local',
          name: 'Local Draft',
          publication_summary: {
            state: 'not_published',
            is_listed: false,
            shared_user_count: 0,
          },
        }),
      ],
      isLoading: false,
    })

    render(<SkillsPageClient />)

    expect(screen.getByRole('button', { name: '자격증명 필요 1개' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '재평가 필요 1개' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '평가 실패 1개' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '공개됨 3개' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '로컬/초안 1개' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '자격증명 필요 1개' }))

    expect(screen.getByText('Credential Setup')).toBeInTheDocument()
    expect(screen.queryByText('Rerun Needed')).not.toBeInTheDocument()
    expect(screen.queryByText('Failed Eval')).not.toBeInTheDocument()
    expect(screen.queryByText('Local Draft')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '로컬/초안 1개' }))

    expect(screen.getByText('Local Draft')).toBeInTheDocument()
    expect(screen.queryByText('Credential Setup')).not.toBeInTheDocument()
  })

  it('opens the create dialog on the conversational tab from the primary CTA', async () => {
    const user = userEvent.setup()
    render(<SkillsPageClient />)

    await user.click(screen.getByRole('button', { name: '대화로 만들기' }))

    expect(screen.getByTestId('skill-create-dialog')).toHaveTextContent('chat')
    expect(mockCreateDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true, initialTab: 'chat' }),
    )
  })
})
