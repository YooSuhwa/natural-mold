import { render, screen } from '../test-utils'
import SkillsPage from '@/app/skills/page'
import type { Skill } from '@/lib/types/skill'

const mockUseSkills = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkills: () => mockUseSkills(),
}))

vi.mock('@/components/skill/skill-create-dialog', () => ({
  SkillCreateDialog: () => null,
}))

vi.mock('@/components/skill/skill-detail-dialog', () => ({
  SkillDetailDialog: () => null,
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
  last_modified_at: '2026-05-01T00:00:00Z',
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-02T00:00:00Z',
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

describe('SkillsPage', () => {
  beforeEach(() => {
    mockUseSkills.mockReturnValue({ data: [skill], isLoading: false })
  })

  it('uses a compact table without the empty credential placeholder column', () => {
    render(<SkillsPage />)

    expect(screen.getByRole('columnheader', { name: '스킬' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '자격증명' })).not.toBeInTheDocument()
    expect(screen.getByText('한국 날씨를 조회합니다.')).toBeInTheDocument()
    expect(screen.getByText('korea-weather')).toBeInTheDocument()
  })

  it('keeps table and grid view controls near the page action', () => {
    render(<SkillsPage />)

    expect(screen.getByRole('tablist', { name: '스킬 보기 모드' })).toHaveClass('ml-auto')
  })
})
