import { render, screen, userEvent } from '../../test-utils'
import { ResourceListState } from '@/components/shared/resource-list-state'

describe('ResourceListState', () => {
  it('renders loading skeleton first', () => {
    render(
      <ResourceListState
        loading
        skeleton={<div data-testid="skeleton">loading</div>}
        emptyTitle="비어 있음"
        filteredEmptyTitle="검색 결과 없음"
      />,
    )

    expect(screen.getByTestId('skeleton')).toBeInTheDocument()
    expect(screen.queryByText('비어 있음')).not.toBeInTheDocument()
  })

  it('renders the base empty state', () => {
    render(
      <ResourceListState
        skeleton={<div />}
        emptyTitle="아직 항목이 없습니다"
        emptyDescription="첫 항목을 만들어 보세요."
        filteredEmptyTitle="검색 결과 없음"
      />,
    )

    expect(screen.getByText('아직 항목이 없습니다')).toBeInTheDocument()
    expect(screen.getByText('첫 항목을 만들어 보세요.')).toBeInTheDocument()
  })

  it('renders filtered empty state with retry action', async () => {
    const user = userEvent.setup()
    let retryCount = 0

    render(
      <ResourceListState
        isFiltered
        skeleton={<div />}
        emptyTitle="비어 있음"
        filteredEmptyTitle="조건에 맞는 항목이 없습니다"
        filteredEmptyDescription="필터를 조정해 보세요."
        retryLabel="필터 초기화"
        onRetry={() => {
          retryCount += 1
        }}
      />,
    )

    await user.click(screen.getByRole('button', { name: '필터 초기화' }))

    expect(screen.getByText('조건에 맞는 항목이 없습니다')).toBeInTheDocument()
    expect(screen.getByText('필터를 조정해 보세요.')).toBeInTheDocument()
    expect(retryCount).toBe(1)
  })

  it('renders error state with retry action', async () => {
    const user = userEvent.setup()
    let retryCount = 0

    render(
      <ResourceListState
        error
        skeleton={<div />}
        emptyTitle="비어 있음"
        filteredEmptyTitle="검색 결과 없음"
        errorTitle="불러오기 실패"
        errorDescription="다시 시도해 주세요."
        onRetry={() => {
          retryCount += 1
        }}
      />,
    )

    await user.click(screen.getByRole('button', { name: '다시 시도' }))

    expect(screen.getByRole('alert')).toHaveTextContent('불러오기 실패')
    expect(screen.getByText('다시 시도해 주세요.')).toBeInTheDocument()
    expect(retryCount).toBe(1)
  })
})
