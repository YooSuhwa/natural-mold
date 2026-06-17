import { describe, expect, it } from 'vitest'

import { render, screen } from '../../../../tests/test-utils'
import { SkillBuilderPreview } from '../skill-builder-preview'
import type { SkillBuilderSession, SkillDraftPackage } from '@/lib/types/skill-builder'

const improveDraft: SkillDraftPackage = {
  name: '회의록 액션 아이템',
  slug: 'meeting-actions',
  description: '마감일과 담당자를 더 엄격하게 추출합니다.',
  files: [
    {
      path: 'SKILL.md',
      content: '개선된 규칙',
      media_type: 'text/markdown',
      role: 'skill',
    },
    {
      path: 'scripts/extract_due_dates.py',
      content: 'print("ok")',
      media_type: 'text/x-python',
      role: 'script',
    },
  ],
  credential_requirements: [],
  execution_profile: {},
  validation_issues: [],
  compatibility_result: null,
  changelog_draft: {
    summary: '마감일 규칙 보강',
    items: [{ title: '날짜 표현을 더 엄격하게 처리', path: 'SKILL.md' }],
  },
  evals: null,
  benchmark: { pass_rate: 0.86, mean_score: 0.82, delta: 0.12 },
}

const improveSession: SkillBuilderSession = {
  id: 'session-1',
  user_id: 'user-1',
  user_request: '날짜 추출을 더 엄격하게 해줘',
  mode: 'improve',
  status: 'review',
  current_phase: 2,
  source_skill_id: 'skill-1',
  base_skill_version: 'v1.0.0',
  base_content_hash: 'hash-before',
  base_snapshot: {
    files: [
      { path: 'SKILL.md', content: '기존 규칙', role: 'skill' },
      { path: 'references/old.md', content: '이전 참고자료', role: 'reference' },
    ],
  },
  draft_package: improveDraft,
  validation_result: {
    error_count: 1,
    warning_count: 1,
    info_count: 1,
    issues: [
      {
        severity: 'error',
        path: 'SKILL.md',
        message: '필수 frontmatter가 없습니다.',
      },
      {
        severity: 'warning',
        path: 'scripts/extract_due_dates.py',
        message: '네트워크 사용 선언이 필요합니다.',
      },
      {
        severity: 'info',
        message: '평가 케이스가 생성되었습니다.',
      },
    ],
  },
  compatibility_result: null,
  changelog_draft: null,
  eval_result: null,
  trigger_eval_result: null,
  finalized_skill_id: null,
  error_message: null,
  created_at: '2026-06-15T00:00:00.000Z',
  updated_at: '2026-06-15T00:00:00.000Z',
}

describe('SkillBuilderPreview', () => {
  it('renders compatibility targets from the builder session result', () => {
    render(
      <SkillBuilderPreview
        session={{
          ...improveSession,
          compatibility_result: {
            targets: {
              openai_codex: { status: 'pass', issues: [] },
              claude_code: { status: 'warning', issues: [] },
              vercel_agent_skills: { status: 'pass', issues: [] },
            },
          },
        }}
        draft={{ ...improveDraft, compatibility_result: null }}
      />,
    )

    expect(screen.getByText('공용 호환성')).toBeInTheDocument()
    expect(screen.getByText('OpenAI/Codex')).toBeInTheDocument()
    expect(screen.getByText('Claude Code')).toBeInTheDocument()
    expect(screen.getByText('Vercel Agent Skills')).toBeInTheDocument()
  })

  it('renders review details for an improve draft', () => {
    render(<SkillBuilderPreview session={improveSession} draft={improveDraft} />)

    expect(screen.getByText('파일 변경 요약')).toBeInTheDocument()
    expect(screen.getByText('원본 2개')).toBeInTheDocument()
    expect(screen.getByText('개선안 2개')).toBeInTheDocument()
    expect(screen.getByText('추가 1')).toBeInTheDocument()
    expect(screen.getByText('수정 1')).toBeInTheDocument()
    expect(screen.getByText('삭제 1')).toBeInTheDocument()
    expect(screen.getByText('SKILL.md · 수정')).toBeInTheDocument()
    expect(screen.getByText('scripts/extract_due_dates.py · 추가')).toBeInTheDocument()
    expect(screen.getByText('references/old.md · 삭제')).toBeInTheDocument()

    expect(screen.getByText('검증 결과')).toBeInTheDocument()
    expect(screen.getByText('오류')).toBeInTheDocument()
    expect(screen.getByText('주의')).toBeInTheDocument()
    expect(screen.getByText('정보')).toBeInTheDocument()
    expect(screen.getByText('SKILL.md: 필수 frontmatter가 없습니다.')).toBeInTheDocument()
    expect(
      screen.getByText('scripts/extract_due_dates.py: 네트워크 사용 선언이 필요합니다.'),
    ).toBeInTheDocument()

    expect(screen.getByText('변경 요약')).toBeInTheDocument()
    expect(screen.getByText('마감일 규칙 보강')).toBeInTheDocument()
    expect(screen.getByText('SKILL.md: 날짜 표현을 더 엄격하게 처리')).toBeInTheDocument()

    expect(screen.getByText('평가')).toBeInTheDocument()
    expect(screen.getByText('통과율 86%')).toBeInTheDocument()
    expect(screen.getByText('평균 점수 0.82')).toBeInTheDocument()
    expect(screen.getByText('변화 +0.12')).toBeInTheDocument()
  })
})
