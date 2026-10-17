import type { ReactNode } from 'react'

import { render, screen, waitFor } from '../../test-utils'
import { SkillTabPageClient } from '@/app/skills/[skillId]/_components/skill-tab-page-client'
import { ApiError } from '@/lib/api/errors'
import type { SkillDetailTabRender } from '@/components/skill/skill-detail-tab-shell'

/** Plan 13.9 row 5 — skill studio tabs: readable with can_view, edits with can_edit,
 * delete can_delete, publish can_share; no can_view → shared 403/404 state. */
const state = vi.hoisted(() => ({
  query: {} as Record<string, unknown>,
  permissions: null as string[] | null,
}))

vi.mock('@/lib/hooks/use-skills', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-skills')>()),
  useSkill: () => state.query,
  useDeleteSkill: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
function editor({ children }: { children: SkillDetailTabRender }): ReactNode {
  return children({
    body: <textarea aria-label="본문" />,
    footer: <button type="button">저장</button>,
  })
}
vi.mock('@/components/skill/skill-detail-text-editor', () => ({ TextSkillEditor: editor }))
vi.mock('@/components/skill/skill-detail-package-editor', () => ({ PackageSkillEditor: editor }))
vi.mock('@/components/skill/skill-history-tab', () => ({ SkillHistoryTab: editor }))
vi.mock('@/components/skill/skill-evaluation-tab', () => ({ SkillEvaluationTab: () => null }))
vi.mock('@/components/skill/skill-metadata-tab', () => ({
  SkillMetadataTab: () => <input aria-label="이름" />,
}))
vi.mock('@/components/skill/skill-credential-bindings-panel', () => ({
  SkillCredentialBindingsPanel: () => null,
}))
vi.mock('@/components/marketplace/publish-wizard', () => ({ PublishWizard: () => null }))
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'me' } }) }))
const OWNER_ANSWER = ['can_use', 'can_view', 'can_edit', 'can_share', 'can_delete']
vi.mock('@/lib/api/resource-permissions', () => ({
  RESOURCE_PERMISSION_BATCH: 100,
  resourcePermissionsApi: {
    list: vi.fn(async (_type: string, ids: string[]) => ({
      // `null` = the legacy owner answer the server sends for the caller's own rows.
      items: ids.map((id) => ({ id, my_permissions: state.permissions ?? OWNER_ANSWER })),
    })),
  },
}))

const skill = {
  id: 'skill-1',
  name: '주간 리포트',
  slug: 'weekly-report',
  kind: 'text',
  used_by_count: 0,
  publication_summary: null,
}

function load(permissions: string[] | null) {
  state.permissions = permissions
  state.query = { data: skill, isLoading: false, isError: false, error: null }
}

describe('skill studio source tab', () => {
  it('owner (legacy body) edits and saves', async () => {
    load(null)
    render(<SkillTabPageClient skillId="skill-1" tab="source" />)
    await waitFor(() => expect(screen.getByLabelText('본문')).toBeEnabled())
    expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
  })

  it('editor edits and saves', async () => {
    load(['can_use', 'can_view', 'can_edit'])
    render(<SkillTabPageClient skillId="skill-1" tab="source" />)
    await waitFor(() => expect(screen.queryByText(/보기 전용이에요/)).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
  })

  it('viewer reads without save', async () => {
    load(['can_use', 'can_view'])
    render(<SkillTabPageClient skillId="skill-1" tab="versions" />)
    await waitFor(() => expect(screen.getByText(/보기 전용이에요/)).toBeInTheDocument())
    expect(screen.getByLabelText('본문')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument()
  })
})

describe('skill studio settings tab', () => {
  it('owner sees publish and delete', async () => {
    load(null)
    render(<SkillTabPageClient skillId="skill-1" tab="settings" />)
    expect(await screen.findByRole('button', { name: /공개하기/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '스킬 삭제' })).toBeInTheDocument()
    expect(screen.getByLabelText('이름')).toBeEnabled()
  })

  it('editor edits metadata but cannot publish or delete', async () => {
    load(['can_use', 'can_view', 'can_edit'])
    render(<SkillTabPageClient skillId="skill-1" tab="settings" />)
    await waitFor(() => expect(screen.getByLabelText('이름')).toBeEnabled())
    expect(screen.queryByRole('button', { name: '스킬 삭제' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /공개하기/ })).not.toBeInTheDocument()
  })

  it('viewer gets a read-only form', async () => {
    load(['can_use', 'can_view'])
    render(<SkillTabPageClient skillId="skill-1" tab="settings" />)
    await waitFor(() => expect(screen.getByLabelText('이름')).toBeDisabled())
  })
})

describe('skill studio without can_view', () => {
  it.each([['runner 403', new ApiError(403, 'permission_required', 'x'), '권한 요청']])(
    '%s offers an access request',
    (_label, error, button) => {
      state.query = { data: undefined, isLoading: false, isError: true, error }
      render(<SkillTabPageClient skillId="skill-1" tab="source" />)
      expect(screen.getByRole('button', { name: button })).toBeInTheDocument()
    },
  )

  it.each([
    [new ApiError(404, 'SKILL_NOT_FOUND', 'x'), '찾을 수 없어요'],
    [new ApiError(429, 'quota_exceeded', 'x'), '사용 한도를 넘었어요'],
    [new ApiError(503, 'authz_unavailable', 'x'), '권한을 확인하지 못했어요'],
  ])('none: %o uses the shared state', (error, title) => {
    state.query = { data: undefined, isLoading: false, isError: true, error }
    render(<SkillTabPageClient skillId="skill-1" tab="source" />)
    expect(screen.getByText(title)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /목록/ })).toHaveAttribute('href', '/skills')
  })
})
