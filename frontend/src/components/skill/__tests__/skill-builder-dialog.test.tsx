import { ApiError } from '@/lib/api/client'
import { skillBuilderApi } from '@/lib/api/skill-builder'
import { streamSkillBuilderMessage } from '@/lib/sse/stream-skill-builder-message'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import { SkillBuilderDialog } from '../skill-builder-dialog'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import type { Skill } from '@/lib/types/skill'
import type { SkillBuilderSession } from '@/lib/types/skill-builder'

type MockSession = {
  readonly data: { readonly is_super_user: boolean } | null
  readonly isPending: boolean
}

const mockUseSession = vi.hoisted(() => vi.fn<() => MockSession>())

vi.mock('@/lib/api/skill-builder', () => ({
  skillBuilderApi: {
    start: vi.fn(),
    get: vi.fn(),
    confirm: vi.fn(),
    runEvaluation: vi.fn(),
  },
}))

vi.mock('@/lib/sse/stream-skill-builder-message', () => ({
  streamSkillBuilderMessage: vi.fn(),
}))

vi.mock('@/lib/auth/session', () => ({
  useSession: mockUseSession,
}))

const skill: Skill = {
  id: 'skill-1',
  name: '회의록 액션 아이템',
  slug: 'meeting-actions',
  description: null,
  kind: 'package',
  version: null,
  storage_path: null,
  content_hash: 'hash-1',
  size_bytes: 0,
  used_by_count: 0,
  package_metadata: null,
  execution_profile: null,
  last_modified_at: '2026-06-15T00:00:00.000Z',
  created_at: '2026-06-15T00:00:00.000Z',
  updated_at: '2026-06-15T00:00:00.000Z',
}

const session: SkillBuilderSession = {
  id: 'session-1',
  user_id: 'user-1',
  user_request: '회의록에서 액션 아이템을 뽑는 스킬',
  mode: 'create',
  status: 'review',
  current_phase: 2,
  draft_package: {
    name: '회의록 액션 아이템',
    slug: 'meeting-actions',
    description: '회의록에서 담당자와 할 일을 추출합니다.',
    files: [
      {
        path: 'SKILL.md',
        content: '# 회의록 액션 아이템',
        media_type: 'text/markdown',
        role: 'skill',
      },
      {
        path: 'agents/openai.yaml',
        content: 'interface: {}',
        media_type: 'text/yaml',
        role: 'metadata',
      },
    ],
    credential_requirements: [],
    execution_profile: {},
    validation_issues: [],
    compatibility_result: {
      targets: {
        openai_codex: { status: 'pass', issues: [] },
      },
      error_count: 0,
      warning_count: 0,
      info_count: 0,
    },
    changelog_draft: { summary: '초안 생성' },
    evals: null,
    benchmark: null,
  },
  validation_result: {
    error_count: 0,
    warning_count: 0,
    issues: [],
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

describe('SkillBuilderDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseSession.mockReturnValue({ data: { is_super_user: false }, isPending: false })
    vi.mocked(skillBuilderApi.get).mockResolvedValue(session)
    vi.mocked(skillBuilderApi.confirm).mockResolvedValue(skill)
    vi.mocked(streamSkillBuilderMessage).mockImplementation(async function* stream() {
      yield { event: 'message_end', data: {} }
    })
  })

  it('shows normal users that an administrator needs to configure the builder model', async () => {
    vi.mocked(skillBuilderApi.start).mockRejectedValue(
      new ApiError(409, 'SYSTEM_LLM_NOT_CONFIGURED', '시스템 LLM 설정이 필요합니다'),
    )

    render(<SkillBuilderDialog open mode="create" onOpenChange={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('요청'), '회의록 스킬을 만들어줘')
    await userEvent.click(screen.getByRole('button', { name: '대화 시작' }))

    expect(await screen.findByText('시스템 LLM 설정이 필요합니다')).toBeInTheDocument()
    expect(
      screen.getByText(
        '스킬 빌더 모델 설정이 필요합니다. 관리자에게 설정을 요청하세요. 텍스트 또는 패키지 업로드는 계속 사용할 수 있습니다.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'System LLM 설정 열기' })).not.toBeInTheDocument()
  })

  it('shows super users a direct System LLM settings action when the builder model is missing', async () => {
    mockUseSession.mockReturnValue({ data: { is_super_user: true }, isPending: false })
    vi.mocked(skillBuilderApi.start).mockRejectedValue(
      new ApiError(409, 'SYSTEM_LLM_NOT_CONFIGURED', '시스템 LLM 설정이 필요합니다'),
    )

    render(<SkillBuilderDialog open mode="create" onOpenChange={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('요청'), '회의록 스킬을 만들어줘')
    await userEvent.click(screen.getByRole('button', { name: '대화 시작' }))

    expect(await screen.findByText('시스템 LLM 설정이 필요합니다')).toBeInTheDocument()
    expect(
      screen.getByText(
        '스킬 빌더는 text_primary 시스템 모델을 사용합니다. System LLM 설정에서 모델과 시스템 자격증명을 연결하세요. 설정 전에도 텍스트 또는 패키지 업로드는 계속 사용할 수 있습니다.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'System LLM 설정 열기' })).toHaveAttribute(
      'href',
      '/settings/system-llm',
    )
  })

  it('starts improve sessions with the source skill id and confirms the created skill', async () => {
    const onCreated = vi.fn()
    const improveSession = { ...session, mode: 'improve' as const }
    vi.mocked(skillBuilderApi.start).mockResolvedValue(improveSession)
    vi.mocked(skillBuilderApi.get).mockResolvedValue(improveSession)

    render(
      <SkillBuilderDialog
        open
        mode="improve"
        sourceSkillId="skill-1"
        onOpenChange={vi.fn()}
        onCreated={onCreated}
      />,
    )

    await userEvent.type(screen.getByLabelText('요청'), '마감일 추출을 더 정확하게 해줘')
    await userEvent.click(screen.getByRole('button', { name: '개선안 만들기' }))

    expect(skillBuilderApi.start).toHaveBeenCalledWith({
      mode: 'improve',
      source_skill_id: 'skill-1',
      user_request: '마감일 추출을 더 정확하게 해줘',
    })
    expect(streamSkillBuilderMessage).toHaveBeenCalledWith(
      'session-1',
      {
        content: '마감일 추출을 더 정확하게 해줘',
      },
      expect.any(AbortSignal),
    )
    expect(await screen.findByText('SKILL.md')).toBeInTheDocument()
    expect(screen.getByText('공용 호환성')).toBeInTheDocument()
    expect(screen.getByText('OpenAI/Codex')).toBeInTheDocument()
    expect(screen.getByText('통과')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '개선 적용' }))

    await waitFor(() => {
      expect(skillBuilderApi.confirm).toHaveBeenCalledWith('session-1')
    })
    expect(onCreated).toHaveBeenCalledWith('skill-1', { openTab: 'content' })
  })

  it('shows a recoverable conflict state when the source skill changed before apply', async () => {
    const onCreated = vi.fn()
    const improveSession = { ...session, mode: 'improve' as const }
    vi.mocked(skillBuilderApi.start).mockResolvedValue(improveSession)
    vi.mocked(skillBuilderApi.get).mockResolvedValue(improveSession)
    vi.mocked(skillBuilderApi.confirm).mockRejectedValue(
      new ApiError(
        409,
        'SKILL_BUILDER_SOURCE_CONFLICT',
        '개선 세션 시작 이후 스킬이 변경되었습니다',
      ),
    )

    render(
      <SkillBuilderDialog
        open
        mode="improve"
        sourceSkillId="skill-1"
        onOpenChange={vi.fn()}
        onCreated={onCreated}
      />,
    )

    await userEvent.type(screen.getByLabelText('요청'), '마감일 추출을 더 정확하게 해줘')
    await userEvent.click(screen.getByRole('button', { name: '개선안 만들기' }))
    await screen.findByText('SKILL.md')
    await userEvent.click(screen.getByRole('button', { name: '개선 적용' }))

    expect(await screen.findByText('스킬이 변경되었습니다')).toBeInTheDocument()
    expect(
      screen.getByText('이 개선 세션을 시작한 뒤 원본 스킬이 변경되었습니다.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '최신 기준으로 다시 만들기' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '세션 버리기' })).toBeEnabled()
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('opens without depending on the normal chat runtime env', () => {
    const previous = process.env.NEXT_PUBLIC_CHAT_RUNTIME
    delete process.env.NEXT_PUBLIC_CHAT_RUNTIME

    try {
      render(<SkillBuilderDialog open mode="create" onOpenChange={vi.fn()} />)
    } finally {
      if (previous === undefined) {
        delete process.env.NEXT_PUBLIC_CHAT_RUNTIME
      } else {
        process.env.NEXT_PUBLIC_CHAT_RUNTIME = previous
      }
    }

    expect(screen.getByRole('heading', { name: '대화로 스킬 만들기' })).toBeInTheDocument()
  })
})
