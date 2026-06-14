import { render, screen, userEvent } from '../test-utils'
import SkillsPage from '@/app/skills/page'
import type { Skill } from '@/lib/types/skill'

const mockUseSkills = vi.fn()
const mockCreateDialog = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: (...args: unknown[]) => mockUseSkills(...args),
}))

vi.mock('@/components/skill/skill-create-dialog', () => ({
  SkillCreateDialog: (props: { readonly open: boolean; readonly initialTab?: string }) => {
    mockCreateDialog(props)
    return props.open ? <div data-testid="skill-create-dialog">{props.initialTab}</div> : null
  },
}))

vi.mock('@/components/skill/skill-detail-dialog', () => ({
  SkillDetailDialog: () => null,
}))

vi.mock('@/components/skill/skill-builder-dialog', () => ({
  SkillBuilderDialog: () => null,
}))

vi.mock('@/components/marketplace/publish-wizard', () => ({
  PublishWizard: () => null,
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

describe('SkillsPage', () => {
  beforeEach(() => {
    mockCreateDialog.mockClear()
    mockUseSkills.mockReturnValue({ data: [skill], isLoading: false })
  })

  it('uses a unified tabbed card panel without the old table chrome', () => {
    render(<SkillsPage />)

    expect(screen.getByRole('tab', { name: '전체 1개' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('스킬 검색')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '스킬' })).not.toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '자격증명' })).not.toBeInTheDocument()
    expect(screen.getByText('한국 날씨를 조회합니다.')).toBeInTheDocument()
    expect(screen.getByText('korea-weather')).toBeInTheDocument()
  })

  it('shows skill health and latest evaluation summary on cards', () => {
    render(<SkillsPage />)

    expect(screen.getByText('검증됨')).toBeInTheDocument()
    expect(screen.getByText('평가 92%')).toBeInTheDocument()
  })

  it('renders skills as catalog-style cards by default', () => {
    render(<SkillsPage />)

    const card = screen.getByText('Korea Weather').closest('article')
    expect(card).toHaveClass('moldy-resource-card')
    expect(card?.className).toMatch(/\bmoldy-tone-card-violet\b/)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('filters skills from the shared tab row', async () => {
    const user = userEvent.setup()
    render(<SkillsPage />)

    await user.click(screen.getByRole('tab', { name: /패키지/ }))

    expect(mockUseSkills).toHaveBeenLastCalledWith({ kind: 'package' })
    expect(screen.getByRole('tab', { name: '패키지 1개' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens the create dialog on the conversational tab from the primary CTA', async () => {
    const user = userEvent.setup()
    render(<SkillsPage />)

    await user.click(screen.getByRole('button', { name: '대화로 만들기' }))

    expect(screen.getByTestId('skill-create-dialog')).toHaveTextContent('chat')
    expect(mockCreateDialog).toHaveBeenLastCalledWith(
      expect.objectContaining({ open: true, initialTab: 'chat' }),
    )
  })
})
