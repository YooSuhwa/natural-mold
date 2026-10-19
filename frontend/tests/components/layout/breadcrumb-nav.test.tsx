import { BreadcrumbNav } from '@/components/layout/breadcrumb-nav'
import { render, screen } from '../../test-utils'

const nav = vi.hoisted(() => ({ pathname: '/' }))
const data = vi.hoisted(() => ({
  agent: undefined as { name: string } | undefined,
  skill: undefined as { name: string } | undefined,
  tenant: undefined as { name: string } | undefined,
  item: undefined as { name: string } | undefined,
}))

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/lib/hooks/use-agents', () => ({ useAgent: () => ({ data: data.agent }) }))
vi.mock('@/lib/hooks/use-conversations', () => ({
  useConversationPages: () => ({ data: undefined }),
}))
vi.mock('@/lib/hooks/use-skills', () => ({ useSkill: () => ({ data: data.skill }) }))
vi.mock('@/lib/hooks/use-marketplace', () => ({
  useMarketplaceItem: () => ({ data: data.item }),
}))
vi.mock('@/lib/hooks/use-platform-tenants', () => ({
  usePlatformTenant: () => ({ data: data.tenant }),
}))

const UUID = '79807784-6484-495e-a0b7-0123456789ab'

function trail(pathname: string): string[] {
  nav.pathname = pathname
  const { unmount } = render(<BreadcrumbNav />)
  const items = [...screen.getByRole('navigation').querySelectorAll('a, [aria-current]')]
    .map((node) => node.textContent ?? '')
    .filter(Boolean)
  unmount()
  return items
}

describe('BreadcrumbNav (org-authz routes)', () => {
  beforeEach(() => {
    data.agent = data.skill = data.tenant = data.item = undefined
  })

  it('labels every settings route of the organization and company screens', () => {
    expect(trail('/settings/company')).toEqual(['설정', '회사'])
    expect(trail('/settings/company/policies')).toEqual(['설정', '회사', '회사 정책'])
    expect(trail('/settings/company/members')).toEqual(['설정', '회사', '회사 구성원'])
    expect(trail('/settings/company/models')).toEqual(['설정', '회사', '모델과 LLM 설정'])
    expect(trail('/settings/company/sso')).toEqual(['설정', '회사', 'SSO와 도메인'])
    expect(trail('/settings/organization')).toEqual(['설정', '조직'])
    expect(trail('/settings/organization/members')).toEqual(['설정', '조직', '멤버'])
    expect(trail('/settings/organization/groups')).toEqual(['설정', '조직', '그룹'])
    expect(trail('/settings/organization/roles')).toEqual(['설정', '조직', '역할 및 권한'])
    expect(trail('/settings/organization/requests')).toEqual(['설정', '조직', '접근 요청'])
    expect(trail('/settings/organization/catalog')).toEqual(['설정', '조직', '카탈로그 정책'])
    expect(trail('/settings/admin/tenants')).toEqual(['설정', '회사 목록'])
    expect(trail('/settings/admin/authz')).toEqual(['설정', '권한 동기화'])
    expect(trail('/settings/admin/audit')).toEqual(['설정', '전체 활동 기록'])
  })

  it('names id segments, with a generic label instead of a raw id', () => {
    expect(trail(`/settings/admin/tenants/${UUID}`)).toEqual(['설정', '회사 목록', '회사 상세'])
    data.tenant = { name: '한빛물산' }
    expect(trail(`/settings/admin/tenants/${UUID}`)).toEqual(['설정', '회사 목록', '한빛물산'])
    expect(trail(`/skills/${UUID}/settings`)).toEqual(['스킬', '스킬 상세', '설정'])
    data.skill = { name: '영업 리포트' }
    expect(trail(`/skills/${UUID}/settings`)).toEqual(['스킬', '영업 리포트', '설정'])
    expect(trail(`/marketplace/${UUID}`)).toEqual(['마켓플레이스', '마켓플레이스 항목'])
    expect(trail(`/marketplace/${UUID}`).join(' ')).not.toContain(UUID.slice(0, 8))
  })

  it('calls a new conversation a new conversation, not agent creation', () => {
    data.agent = { name: '사내 IT 헬프데스크' }
    expect(trail(`/agents/${UUID}/conversations/new`)).toEqual(['사내 IT 헬프데스크', '새 대화'])
    expect(trail('/agents/new')).toEqual(['에이전트 만들기'])
  })

  it('labels the trace debugger segment', () => {
    data.agent = { name: '사내 IT 헬프데스크' }
    expect(trail(`/agents/${UUID}/conversations/${UUID}/traces`)).toEqual([
      '사내 IT 헬프데스크',
      '대화',
      '실행 추적',
    ])
  })

  it('never shows an invitation token', () => {
    expect(trail('/invite/ov1.secret-token')).toEqual(['초대'])
  })
})
