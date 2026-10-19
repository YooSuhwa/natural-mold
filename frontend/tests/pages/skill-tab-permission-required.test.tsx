import { SkillTabPageClient } from '@/app/skills/[skillId]/_components/skill-tab-page-client'
import { ApiError } from '@/lib/api/errors'
import { render, screen } from '../test-utils'

const skillQuery = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))

vi.mock('@/lib/hooks/use-skills', () => ({ useSkill: () => skillQuery.current }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/skill/skill-detail-package-editor', () => ({
  PackageSkillEditor: () => null,
}))
vi.mock('@/components/skill/skill-detail-text-editor', () => ({ TextSkillEditor: () => null }))
vi.mock('@/components/skill/skill-evaluation-tab', () => ({ SkillEvaluationTab: () => null }))
vi.mock('@/components/skill/skill-history-tab', () => ({ SkillHistoryTab: () => null }))

describe('SkillTabPageClient access states', () => {
  it('offers an access request on a 403 permission_required (plan 8.4, S18)', () => {
    const error = new ApiError(403, 'permission_required', 'x', { request_access: true })
    skillQuery.current = { data: undefined, isLoading: false, isError: true, error }
    render(<SkillTabPageClient skillId="skill-1" tab="settings" />)
    expect(screen.getByText('권한이 필요해요')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /목록/ })).toHaveAttribute('href', '/skills')
  })

  it('keeps the unavailable state for other errors (404 stays a dead end)', () => {
    const error = new ApiError(404, 'not_found', 'x')
    skillQuery.current = { data: undefined, isLoading: false, isError: true, error }
    render(<SkillTabPageClient skillId="skill-1" tab="settings" />)
    expect(screen.queryByRole('button', { name: '권한 요청' })).not.toBeInTheDocument()
  })
})
