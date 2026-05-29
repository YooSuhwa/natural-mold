import { render, screen, userEvent } from '../../test-utils'
import { ToolCatalog } from '@/components/tool/tool-catalog'
import type { ToolDefinition } from '@/lib/types/tool'

const mockUseToolTypes = vi.fn()

vi.mock('@/lib/hooks/use-tools', () => ({
  useToolTypes: () => mockUseToolTypes(),
}))

const definitions: ToolDefinition[] = [
  {
    key: 'naver_news',
    display_name: '네이버 뉴스 검색',
    description: '뉴스를 검색합니다.',
    icon_id: 'search',
    category: 'search',
    parameters: [],
    credential_definition_keys: ['naver_search'],
    requires_credential: true,
  },
]

describe('ToolCatalog', () => {
  beforeEach(() => {
    mockUseToolTypes.mockReturnValue({ data: definitions, isLoading: false })
  })

  it('uses Korean catalog labels and credential badge', () => {
    render(<ToolCatalog onPick={vi.fn()} />)

    expect(screen.getByText('카테고리')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '전체' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('도구 검색')).toBeInTheDocument()
    expect(screen.getByText('자격증명 필요')).toBeInTheDocument()
  })

  it('uses Korean empty state copy after filtering', async () => {
    const user = userEvent.setup()
    render(<ToolCatalog onPick={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('도구 검색'), '없는 도구')

    expect(screen.getByText('조건에 맞는 도구가 없어요')).toBeInTheDocument()
    expect(screen.getByText('다른 카테고리나 검색어를 시도해 보세요.')).toBeInTheDocument()
  })
})
