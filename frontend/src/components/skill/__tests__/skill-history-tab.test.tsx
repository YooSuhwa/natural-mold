import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent, within } from '../../../../tests/test-utils'
import type { SkillRevisionDetail, SkillRevisionSummary } from '@/lib/types/skill-revision'

import type { SkillDetailTabSlots } from '../skill-detail-tab-shell'

// 구 DialogShell 렌더러 삭제(Phase 2) — 테스트는 슬롯을 평면 렌더한다.
function renderTestSlots(slots: SkillDetailTabSlots) {
  return (
    <>
      {slots.sidebar}
      {slots.body}
      {slots.footer}
      {slots.overlay}
    </>
  )
}
import { SkillHistoryTab } from '../skill-history-tab'

const mockUseSkillRevisions = vi.fn()
const mockUseSkillRevision = vi.fn()
const mockRollback = vi.fn()
const mockUseRollbackSkillRevision = vi.fn()

vi.mock('@/lib/hooks/use-skill-revisions', () => ({
  useSkillRevisions: (...args: readonly unknown[]) => mockUseSkillRevisions(...args),
  useSkillRevision: (...args: readonly unknown[]) => mockUseSkillRevision(...args),
  useRollbackSkillRevision: (...args: readonly unknown[]) => mockUseRollbackSkillRevision(...args),
  // M4 diff 카드 — 이 테스트의 관심사가 아니라 로딩 상태로 고정.
  useSkillRevisionFiles: () => ({ data: undefined, isLoading: true, isError: false }),
  useSkillRevisionFileContent: () => ({ data: undefined, isLoading: true, isError: false }),
}))

// Phase 3 — 히스토리 탭이 리비전 통과율 배지용으로 version-stats를 조회한다.
vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationVersionStats: () => ({ data: [], isLoading: false }),
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

function buildRevisionDetail(overrides: Partial<SkillRevisionDetail>): SkillRevisionDetail {
  return {
    ...buildRevision(overrides),
    changed_files: null,
    changelog_items: null,
    compatibility_result: null,
    evaluation_summary: null,
    metadata_json: {},
    ...overrides,
  }
}

describe('SkillHistoryTab', () => {
  beforeEach(() => {
    mockUseSkillRevisions.mockReset()
    mockUseSkillRevision.mockReset()
    mockRollback.mockReset()
    mockUseRollbackSkillRevision.mockReset()
    mockUseSkillRevision.mockReturnValue({ data: undefined, isLoading: false })
    mockUseRollbackSkillRevision.mockReturnValue({
      mutate: mockRollback,
      isPending: false,
    })
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

    render(<SkillHistoryTab skillId="skill-1">{renderTestSlots}</SkillHistoryTab>)

    const revisions = screen.getAllByRole('article')
    expect(within(revisions[0]).getByText('리비전 3')).toBeInTheDocument()
    expect(within(revisions[0]).getByText('현재 버전')).toBeInTheDocument()
    expect(within(revisions[0]).getByText(/빌더 개선/)).toBeInTheDocument()
    expect(within(revisions[0]).getByText('3개 파일')).toBeInTheDocument()
    expect(within(revisions[1]).getByText('리비전 2')).toBeInTheDocument()
    expect(within(revisions[2]).getByText('리비전 1')).toBeInTheDocument()
  })

  it('shows selected revision detail and disables rollback for the current revision', async () => {
    const details: Readonly<Record<string, SkillRevisionDetail>> = {
      'rev-2': buildRevisionDetail({
        id: 'rev-2',
        revision_number: 2,
        operation: 'manual_content_update',
        changed_files: [{ path: 'SKILL.md', status: 'modified' }],
        changelog_items: [{ title: '지침을 더 구체화', path: 'SKILL.md' }],
        compatibility_result: { targets: { openai_codex: { status: 'ok' } } },
        evaluation_summary: { status: 'completed', mean_score: 0.82 },
      }),
      'rev-3': buildRevisionDetail({
        id: 'rev-3',
        revision_number: 3,
        operation: 'builder_improvement',
        changelog_summary: '최신 개선',
      }),
    }
    mockUseSkillRevisions.mockReturnValue({
      data: [
        buildRevision({ id: 'rev-2', revision_number: 2, operation: 'manual_content_update' }),
        buildRevision({
          id: 'rev-3',
          revision_number: 3,
          operation: 'builder_improvement',
          changelog_summary: '최신 개선',
          created_at: '2026-06-03T00:00:00Z',
        }),
      ],
      isLoading: false,
    })
    mockUseSkillRevision.mockImplementation(
      (_skillId: string | null | undefined, revisionId: string | null | undefined) => ({
        data: revisionId ? details[revisionId] : undefined,
        isLoading: false,
      }),
    )

    render(<SkillHistoryTab skillId="skill-1">{renderTestSlots}</SkillHistoryTab>)

    expect(screen.getByText('리비전 3 상세')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '리비전 3 되돌리기' })).toBeDisabled()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '리비전 2 보기' }))

    expect(screen.getByText('리비전 2 상세')).toBeInTheDocument()
    expect(screen.getByText('지침을 더 구체화 · SKILL.md')).toBeInTheDocument()
    expect(screen.getByText('SKILL.md · modified')).toBeInTheDocument()
    expect(screen.getByText('공용 호환성')).toBeInTheDocument()
    expect(screen.getByText('OpenAI/Codex')).toBeInTheDocument()
    expect(screen.getByText('통과')).toBeInTheDocument()
    expect(screen.getByText('mean_score: 0.82')).toBeInTheDocument()
  })

  it('confirms rollback for a previous revision', async () => {
    const details: Readonly<Record<string, SkillRevisionDetail>> = {
      'rev-1': buildRevisionDetail({ id: 'rev-1', revision_number: 1 }),
      'rev-2': buildRevisionDetail({ id: 'rev-2', revision_number: 2 }),
    }
    mockUseSkillRevisions.mockReturnValue({
      data: [
        buildRevision({ id: 'rev-1', revision_number: 1 }),
        buildRevision({
          id: 'rev-2',
          revision_number: 2,
          operation: 'manual_content_update',
          created_at: '2026-06-02T00:00:00Z',
        }),
      ],
      isLoading: false,
    })
    mockUseSkillRevision.mockImplementation(
      (_skillId: string | null | undefined, revisionId: string | null | undefined) => ({
        data: revisionId ? details[revisionId] : undefined,
        isLoading: false,
      }),
    )

    render(<SkillHistoryTab skillId="skill-1">{renderTestSlots}</SkillHistoryTab>)

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '리비전 1 보기' }))
    await user.click(screen.getByRole('button', { name: '리비전 1 되돌리기' }))

    expect(
      screen.getByText('이전 버전으로 되돌리면 현재 내용은 새 이력으로 보존됩니다.'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '되돌리기' }))

    expect(mockRollback).toHaveBeenCalledWith(
      'rev-1',
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })

  it('keeps the legacy empty state for skills without revisions', () => {
    mockUseSkillRevisions.mockReturnValue({ data: [], isLoading: false })

    render(<SkillHistoryTab skillId="skill-1">{renderTestSlots}</SkillHistoryTab>)

    expect(screen.getByText('현재 버전부터 이력이 쌓입니다.')).toBeInTheDocument()
  })
})
