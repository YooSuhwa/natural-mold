import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, within } from '../../../../tests/test-utils'
import type { SkillRevisionSummary } from '@/lib/types/skill-revision'

import { SkillHistoryTab } from '../skill-history-tab'

const mockUseSkillRevisions = vi.fn()

vi.mock('@/lib/hooks/use-skill-revisions', () => ({
  useSkillRevisions: (...args: readonly unknown[]) => mockUseSkillRevisions(...args),
}))

function buildRevision(overrides: Partial<SkillRevisionSummary>): SkillRevisionSummary {
  return {
    id: 'rev-1',
    skill_id: 'skill-1',
    revision_number: 1,
    operation: 'create',
    skill_version: '0.1.0',
    content_hash: 'hash-1',
    size_bytes: 128,
    file_count: 1,
    changelog_summary: '초기 생성',
    created_at: '2026-06-01T00:00:00Z',
    ...overrides,
  }
}

describe('SkillHistoryTab', () => {
  beforeEach(() => {
    mockUseSkillRevisions.mockReset()
  })

  it('renders revisions newest first and marks the current revision', () => {
    mockUseSkillRevisions.mockReturnValue({
      data: [
        buildRevision({
          id: 'rev-1',
          revision_number: 1,
          operation: 'create',
          content_hash: '111111111111',
          changelog_summary: '처음 생성',
        }),
        buildRevision({
          id: 'rev-3',
          revision_number: 3,
          operation: 'builder_improvement',
          content_hash: '333333333333',
          file_count: 3,
          changelog_summary: '날씨 요약 규칙 개선',
          created_at: '2026-06-03T00:00:00Z',
        }),
        buildRevision({
          id: 'rev-2',
          revision_number: 2,
          operation: 'manual_content_update',
          content_hash: '222222222222',
          file_count: 2,
          changelog_summary: '문구 수정',
          created_at: '2026-06-02T00:00:00Z',
        }),
      ],
      isLoading: false,
    })

    render(<SkillHistoryTab skillId="skill-1" onClose={vi.fn()} />)

    const revisions = screen.getAllByRole('article')
    expect(within(revisions[0]).getByText('리비전 3')).toBeInTheDocument()
    expect(within(revisions[0]).getByText('현재 버전')).toBeInTheDocument()
    expect(within(revisions[0]).getByText(/빌더 개선/)).toBeInTheDocument()
    expect(within(revisions[0]).getByText('3개 파일')).toBeInTheDocument()
    expect(within(revisions[1]).getByText('리비전 2')).toBeInTheDocument()
    expect(within(revisions[2]).getByText('리비전 1')).toBeInTheDocument()
  })

  it('keeps the legacy empty state for skills without revisions', () => {
    mockUseSkillRevisions.mockReturnValue({ data: [], isLoading: false })

    render(<SkillHistoryTab skillId="skill-1" onClose={vi.fn()} />)

    expect(screen.getByText('현재 버전부터 이력이 쌓입니다.')).toBeInTheDocument()
  })
})
