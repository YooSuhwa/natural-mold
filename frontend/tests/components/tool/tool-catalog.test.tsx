import { render, screen } from '../../test-utils'
import { ToolCatalog } from '@/app/tools/_components/tool-catalog'
import type { ToolDefinition } from '@/lib/types/tool'

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
  it('uses Korean catalog labels and credential badge', () => {
    render(
      <ToolCatalog
        category="all"
        definitions={definitions}
        isLoading={false}
        search=""
        onPick={vi.fn()}
      />,
    )

    expect(screen.queryByText('카테고리')).not.toBeInTheDocument()
    expect(screen.getByText('자격증명 필요')).toBeInTheDocument()
  })

  it('uses template-style card treatment for catalog tools', () => {
    render(
      <ToolCatalog
        category="all"
        definitions={definitions}
        isLoading={false}
        search=""
        onPick={vi.fn()}
      />,
    )

    const card = screen.getByRole('button', { name: /네이버 뉴스 검색/ })

    expect(card).toHaveClass('moldy-resource-card')
    expect(card.className).toMatch(/\bmoldy-tone-card-sky\b/)
    expect(card.querySelector('svg')?.parentElement?.className).toMatch(/\bmoldy-tone-icon-sky\b/)
  })

  it('uses Korean empty state copy after filtering', async () => {
    render(
      <ToolCatalog
        category="all"
        definitions={definitions}
        isLoading={false}
        search="없는 도구"
        onPick={vi.fn()}
      />,
    )

    expect(screen.getByText('조건에 맞는 도구가 없어요')).toBeInTheDocument()
    expect(screen.getByText('다른 카테고리나 검색어를 시도해 보세요.')).toBeInTheDocument()
  })
})
