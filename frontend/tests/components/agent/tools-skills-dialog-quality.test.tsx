import { describe, expect, it, vi } from 'vitest'

import { CurrentColumn } from '@/components/agent/visual-settings/dialogs/tools-skills-current-column'
import { SkillsPanel } from '@/components/agent/visual-settings/dialogs/tools-skills-resource-panels'
import type { Skill } from '@/lib/types/skill'

import { render, screen } from '../../test-utils'

const now = '2026-06-15T00:00:00.000Z'

const evaluatedSkill: Skill = {
  id: 'skill-1',
  name: '회의록 정리',
  slug: 'meeting-summary',
  description: '회의록에서 결정사항과 액션 아이템을 정리합니다.',
  kind: 'package',
  version: '1.0.0',
  storage_path: null,
  content_hash: 'hash-1',
  size_bytes: 1024,
  used_by_count: 2,
  package_metadata: null,
  execution_profile: null,
  current_revision_id: 'revision-1',
  latest_evaluation_summary: {
    status: 'completed',
    latest_run_id: 'run-1',
    evaluation_set_id: 'set-1',
    pass_rate: 0.92,
    skill_content_hash: 'hash-1',
    created_at: now,
    completed_at: now,
  },
  health: {
    state: 'needs_credentials',
    label: '자격증명 필요',
    reason: 'Missing required credential bindings.',
    severity: 'warning',
  },
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

describe('ToolsSkillsDialog skill quality summaries', () => {
  it('shows compact skill quality in the available skill picker without rerun controls', () => {
    render(
      <SkillsPanel
        allSkills={[evaluatedSkill]}
        selectedSkillIds={new Set()}
        onToggle={vi.fn()}
      />,
    )

    expect(screen.getByText('회의록 정리')).toBeInTheDocument()
    expect(screen.getByText('자격증명 필요')).toBeInTheDocument()
    expect(screen.getByText('평가 92%')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /평가 다시 실행/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /평가 취소/ })).not.toBeInTheDocument()
  })

  it('shows compact quality for selected skills without exposing evaluation actions', () => {
    render(
      <CurrentColumn
        total={1}
        tools={[]}
        mcpTools={[]}
        skills={[evaluatedSkill]}
        onRemoveTool={vi.fn()}
        onRemoveMcp={vi.fn()}
        onRemoveSkill={vi.fn()}
      />,
    )

    expect(screen.getByText('회의록 정리')).toBeInTheDocument()
    expect(screen.getByText('자격증명 필요')).toBeInTheDocument()
    expect(screen.getByText('평가 92%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '회의록 정리 제거' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /평가 다시 실행/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /평가 취소/ })).not.toBeInTheDocument()
  })
})
