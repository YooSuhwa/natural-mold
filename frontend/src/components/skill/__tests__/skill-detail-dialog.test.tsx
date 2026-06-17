import { describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import type { Skill } from '@/lib/types/skill'

import { SkillDetailDialog } from '../skill-detail-dialog'

const mockUseSkill = vi.fn()
const mockUseSkillFiles = vi.fn()
const mockUseSkillContent = vi.fn()
const mockUpdateSkillMetadata = vi.fn()
const mockUpdateSkillContent = vi.fn()
const mockSetSkillFile = vi.fn()
const mockDeleteSkillFile = vi.fn()
const mockDeleteSkill = vi.fn()
const mockUseSkillEvaluationSets = vi.fn()
const mockUseSkillEvaluationRuns = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkill: (...args: readonly unknown[]) => mockUseSkill(...args),
  useSkillFiles: (...args: readonly unknown[]) => mockUseSkillFiles(...args),
  useSkillContent: (...args: readonly unknown[]) => mockUseSkillContent(...args),
  useUpdateSkillMetadata: () => ({
    mutateAsync: mockUpdateSkillMetadata,
    isPending: false,
  }),
  useUpdateSkillContent: () => ({
    mutateAsync: mockUpdateSkillContent,
    isPending: false,
  }),
  useSetSkillFile: () => ({
    mutateAsync: mockSetSkillFile,
    isPending: false,
  }),
  useDeleteSkillFile: () => ({
    mutateAsync: mockDeleteSkillFile,
    isPending: false,
  }),
  useDeleteSkill: () => ({
    mutateAsync: mockDeleteSkill,
    isPending: false,
  }),
}))

vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationSets: (...args: readonly unknown[]) => mockUseSkillEvaluationSets(...args),
  useSkillEvaluationRuns: (...args: readonly unknown[]) => mockUseSkillEvaluationRuns(...args),
  useEstimateSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useCreateSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useCancelSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}))

function buildSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    id: 'skill-1',
    name: '회의록 정리',
    slug: 'meeting-notes',
    description: '회의록에서 액션 아이템을 정리합니다.',
    kind: 'package',
    version: '0.1.0',
    storage_path: null,
    content_hash: 'hash-current',
    size_bytes: 1000,
    used_by_count: 0,
    package_metadata: null,
    credential_requirements: null,
    execution_profile: null,
    current_revision_id: null,
    latest_evaluation_summary: {
      status: 'completed',
      latest_run_id: 'run-1',
      evaluation_set_id: 'set-1',
      pass_rate: 0.9,
      skill_content_hash: 'hash-current',
      created_at: '2026-06-01T00:00:00Z',
      completed_at: '2026-06-01T00:01:00Z',
    },
    health: null,
    last_modified_at: '2026-06-01T00:00:00Z',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    origin_summary: null,
    publication_summary: null,
    installation: null,
    ...overrides,
  }
}

function mockFileFetch(contents: Record<string, string>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const raw = String(input)
      const path = decodeURI(raw.split('/files/')[1] ?? '')
      return Promise.resolve({
        ok: true,
        text: () => Promise.resolve(contents[path] ?? ''),
      } as Response)
    }),
  )
}

describe('SkillDetailDialog', () => {
  beforeEach(() => {
    mockUseSkill.mockReset()
    mockUseSkillFiles.mockReset()
    mockUseSkillContent.mockReset()
    mockUpdateSkillMetadata.mockReset()
    mockUpdateSkillContent.mockReset()
    mockSetSkillFile.mockReset()
    mockDeleteSkillFile.mockReset()
    mockDeleteSkill.mockReset()
    mockUseSkillEvaluationSets.mockReset()
    mockUseSkillEvaluationRuns.mockReset()
    vi.unstubAllGlobals()
    mockUseSkillFiles.mockReturnValue({ data: [] })
    mockUseSkillContent.mockReturnValue({ data: { content: '' } })
    mockUpdateSkillContent.mockResolvedValue(undefined)
    mockSetSkillFile.mockResolvedValue(undefined)
    mockDeleteSkillFile.mockResolvedValue(undefined)
    mockDeleteSkill.mockResolvedValue(undefined)
  })

  it('renders one shell body and footer for the evaluation tab', () => {
    mockUseSkill.mockReturnValue({ data: buildSkill() })
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [],
      isLoading: false,
    })
    mockUseSkillEvaluationRuns.mockReturnValue({
      data: [],
      isLoading: false,
    })

    render(
      <SkillDetailDialog skillId="skill-1" open onOpenChange={vi.fn()} initialTab="evaluation" />,
    )

    expect(document.body.querySelectorAll('.moldy-dialog-body')).toHaveLength(1)
    expect(document.body.querySelectorAll('.moldy-dialog-footer')).toHaveLength(1)
    expect(screen.getByText('아직 평가 세트가 없습니다')).toBeInTheDocument()
  })

  it('renders one shell body and footer for the metadata tab', () => {
    mockUseSkill.mockReturnValue({ data: buildSkill() })

    render(
      <SkillDetailDialog skillId="skill-1" open onOpenChange={vi.fn()} initialTab="metadata" />,
    )

    expect(document.body.querySelectorAll('.moldy-dialog-body')).toHaveLength(1)
    expect(document.body.querySelectorAll('.moldy-dialog-footer')).toHaveLength(1)
    expect(screen.getByText('이름')).toBeInTheDocument()
  })

  it('keeps package file selection, save, delete, and footer actions in one shell', async () => {
    const user = userEvent.setup()
    mockUseSkill.mockReturnValue({ data: buildSkill() })
    mockUseSkillFiles.mockReturnValue({
      data: [
        { path: 'SKILL.md', size: 40, is_dir: false },
        { path: 'scripts/main.py', size: 15, is_dir: false },
      ],
    })
    mockFileFetch({
      'SKILL.md': 'Skill instructions',
      'scripts/main.py': 'print("hello")\n',
    })

    render(<SkillDetailDialog skillId="skill-1" open onOpenChange={vi.fn()} />)

    expect(await screen.findByDisplayValue('Skill instructions')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /main.py/ }))
    const editor = await screen.findByDisplayValue(/print\("hello"\)/)
    await user.clear(editor)
    await user.type(editor, 'print("updated")')
    await user.click(screen.getByRole('button', { name: '파일 저장' }))

    await waitFor(() => {
      expect(mockSetSkillFile).toHaveBeenCalledWith({
        path: 'scripts/main.py',
        content: 'print("updated")',
      })
    })

    await user.click(screen.getByRole('button', { name: '파일 삭제' }))
    await user.click(screen.getByRole('button', { name: '삭제 확인' }))

    await waitFor(() => {
      expect(mockDeleteSkillFile).toHaveBeenCalledWith('scripts/main.py')
    })

    const footer = document.body.querySelector('.moldy-dialog-footer')
    expect(document.body.querySelectorAll('.moldy-dialog-body')).toHaveLength(1)
    expect(document.body.querySelectorAll('.moldy-dialog-footer')).toHaveLength(1)
    expect(footer).not.toBeNull()
    expect(
      within(footer as HTMLElement).getByRole('button', { name: '스킬 삭제' }),
    ).toBeInTheDocument()
    expect(within(footer as HTMLElement).getByRole('button', { name: '닫기' })).toBeInTheDocument()
    expect(
      within(footer as HTMLElement).getByRole('button', { name: '파일 저장' }),
    ).toBeInTheDocument()
  })

  it('keeps text skill save behavior in the single shell footer', async () => {
    const user = userEvent.setup()
    mockUseSkill.mockReturnValue({ data: buildSkill({ kind: 'text' }) })
    mockUseSkillContent.mockReturnValue({
      data: { content: 'Original text skill' },
    })

    render(<SkillDetailDialog skillId="skill-1" open onOpenChange={vi.fn()} />)

    const editor = await screen.findByDisplayValue('Original text skill')
    await user.clear(editor)
    await user.type(editor, 'Updated text skill')
    await user.click(screen.getByRole('button', { name: '저장' }))

    await waitFor(() => {
      expect(mockUpdateSkillContent).toHaveBeenCalledWith({
        id: 'skill-1',
        data: { content: 'Updated text skill' },
      })
    })

    const footer = document.body.querySelector('.moldy-dialog-footer')
    expect(document.body.querySelectorAll('.moldy-dialog-body')).toHaveLength(1)
    expect(document.body.querySelectorAll('.moldy-dialog-footer')).toHaveLength(1)
    expect(footer).not.toBeNull()
    expect(
      within(footer as HTMLElement).getByRole('button', { name: '스킬 삭제' }),
    ).toBeInTheDocument()
    expect(within(footer as HTMLElement).getByRole('button', { name: '닫기' })).toBeInTheDocument()
    expect(within(footer as HTMLElement).getByRole('button', { name: '저장' })).toBeInTheDocument()
  })
})
