import { render, screen, userEvent, waitFor, within } from '../../test-utils'
import { SkillsPageClient } from '@/app/skills/_components/skills-page-client'
import { SkillBuilderIndexClient } from '@/app/skills/builder/_components/skill-builder-index-client'
import type { Skill } from '@/lib/types/skill'

/** Plan 13.9 row 5 — skill list and builder: row actions by can_view/can_edit/can_delete,
 * creation (builder, upload) by cap_create_skill. */
const state = vi.hoisted(() => ({
  permissions: null as string[] | null,
  org: { enforced: false, capabilities: [] as string[] },
}))

const skill: Skill = {
  id: 'skill-1',
  name: '주간 리포트',
  slug: 'weekly-report',
  description: null,
  kind: 'package',
  version: '1.0.0',
  storage_path: null,
  content_hash: null,
  size_bytes: 10,
  used_by_count: 0,
  package_metadata: null,
  execution_profile: null,
  last_modified_at: '2026-10-01T00:00:00Z',
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams('skillId=skill-1'),
}))
vi.mock('@/lib/hooks/use-skills', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-skills')>()),
  useSkills: () => ({ data: [skill], isLoading: false }),
  useSkill: () => ({ data: skill }),
  useDeleteSkill: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-skill-builder', () => ({
  useBuilderSessionLauncher: () => ({
    pending: false,
    startCreate: vi.fn(),
    startImprove: vi.fn(),
  }),
  useSkillBuilderSessions: () => ({ data: [], isLoading: false }),
}))
vi.mock('@/lib/hooks/use-agents', () => ({ useAgents: () => ({ data: [] }) }))
vi.mock('@/components/skill/skill-create-dialog', () => ({ SkillCreateDialog: () => null }))
vi.mock('@/components/marketplace/publish-wizard', () => ({ PublishWizard: () => null }))
vi.mock('@/lib/auth/session', () => ({ useSession: () => ({ data: { id: 'me' } }) }))
vi.mock('@/components/sharing/resource-share-dialog', () => ({
  ResourceShareDialog: (props: { open: boolean; resourceType: string; resourceId: string }) =>
    props.open ? (
      <div role="dialog" aria-label="share">
        {props.resourceType}:{props.resourceId}
      </div>
    ) : null,
}))
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
vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: {
    list: vi.fn(async () => [
      {
        id: 'org-1',
        is_active: true,
        roles: [],
        capabilities: state.org.capabilities,
        capabilities_enforced: state.org.enforced,
      },
    ]),
  },
}))

function row() {
  return within(screen.getByRole('row', { name: /주간 리포트/ }))
}
const IMPROVE = '주간 리포트 대화로 개선'

async function openRowMenu() {
  await userEvent.click(screen.getByRole('button', { name: '주간 리포트 추가 작업' }))
  await screen.findAllByRole('menuitem')
}

describe('skill list row actions by permission level', () => {
  beforeEach(() => {
    state.org = { enforced: false, capabilities: [] }
  })

  it('owner (legacy body): improve, evaluation and delete', async () => {
    state.permissions = null
    render(<SkillsPageClient />)
    expect(await row().findByRole('button', { name: IMPROVE })).toBeInTheDocument()
    expect(row().getByRole('button', { name: '평가' })).toBeInTheDocument()
    await openRowMenu()
    expect(await screen.findByRole('menuitem', { name: /삭제/ })).toBeInTheDocument()
  })

  it('owner: shares the skill from the row menu (can_share)', async () => {
    state.permissions = null
    render(<SkillsPageClient />)
    await row().findByRole('button', { name: IMPROVE })
    await openRowMenu()
    await userEvent.click(await screen.findByRole('menuitem', { name: /공유/ }))
    expect(await screen.findByRole('dialog', { name: 'share' })).toHaveTextContent('skill:skill-1')
  })

  it('editor: improve without delete or publish', async () => {
    state.permissions = ['can_use', 'can_view', 'can_edit']
    render(<SkillsPageClient />)
    expect(await row().findByRole('button', { name: IMPROVE })).toBeInTheDocument()
    expect(row().getByRole('button', { name: '평가' })).toBeInTheDocument()
    await openRowMenu()
    expect(screen.queryByRole('menuitem', { name: /삭제/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /공개하기/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /^공유$/ })).not.toBeInTheDocument()
  })

  it('viewer: evaluation and versions, no improve', async () => {
    state.permissions = ['can_use', 'can_view']
    render(<SkillsPageClient />)
    await waitFor(() => expect(row().queryByRole('button', { name: IMPROVE })).toBeNull())
    expect(row().getByRole('button', { name: '평가' })).toBeInTheDocument()
    expect(row().getByRole('button', { name: '버전' })).toBeInTheDocument()
  })

  it('runner: no studio entry points', async () => {
    state.permissions = ['can_use']
    render(<SkillsPageClient />)
    await waitFor(() => expect(row().queryByRole('button', { name: '평가' })).toBeNull())
    expect(row().queryByRole('button', { name: IMPROVE })).toBeNull()
  })

  it('creation is hidden without cap_create_skill when enforced', async () => {
    state.permissions = null
    state.org = { enforced: true, capabilities: [] }
    render(<SkillsPageClient />)
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '패키지 업로드' })).not.toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: /대화로 만들기/ })).not.toBeInTheDocument()
  })

  it('creation is shown with cap_create_skill', async () => {
    state.org = { enforced: true, capabilities: ['cap_create_skill'] }
    render(<SkillsPageClient />)
    expect(await screen.findByRole('button', { name: '패키지 업로드' })).toBeInTheDocument()
  })
})

describe('skill builder index', () => {
  it('hides create without the capability and improve without can_edit', async () => {
    state.org = { enforced: true, capabilities: [] }
    state.permissions = ['can_use', 'can_view']
    render(<SkillBuilderIndexClient />)
    await waitFor(() => expect(screen.queryAllByRole('button')).toHaveLength(0))
  })

  it('editor with the capability sees both entries', async () => {
    state.org = { enforced: true, capabilities: ['cap_create_skill'] }
    state.permissions = ['can_use', 'can_view', 'can_edit']
    render(<SkillBuilderIndexClient />)
    expect(await screen.findByRole('button', { name: /주간 리포트/ })).toBeInTheDocument()
    expect(screen.getAllByRole('button').length).toBeGreaterThanOrEqual(2)
  })
})
