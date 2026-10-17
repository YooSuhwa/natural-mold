import { ApiErrorState, useApiErrorMessage } from '@/components/shared/api-error-state'
import { ApiError } from '@/lib/api/errors'
import { render, screen, userEvent } from '../../test-utils'

vi.mock('@/lib/hooks/use-readiness', () => ({
  useAgentReadiness: () => ({ data: undefined, isError: false, refetch: vi.fn() }),
}))

describe('ApiErrorState (plan 13.1 shared error display)', () => {
  it('offers an access request for a visible resource without permission', () => {
    const error = new ApiError(403, 'permission_required', 'x', { request_access: true })
    render(<ApiErrorState error={error} resource={{ type: 'tool', id: 't-1' }} />)
    expect(screen.getByText('권한이 필요해요')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '권한 요청' })).toBeInTheDocument()
  })

  it.each([
    [new ApiError(401, 'unauthorized', 'x'), '다시 로그인해 주세요'],
    [new ApiError(403, 'permission_required', 'x'), '권한이 필요해요'],
    [new ApiError(403, 'capability_required', 'x'), '조직 기능 권한이 필요해요'],
    [new ApiError(403, 'org_access_denied', 'x'), '이 조직에 접근할 수 없어요'],
    [new ApiError(404, 'not_found', 'x'), '찾을 수 없어요'],
    [new ApiError(429, 'quota_exceeded', 'x'), '사용 한도를 넘었어요'],
    [new ApiError(503, 'authz_unavailable', 'x'), '권한을 확인하지 못했어요'],
  ])('shows the shared state for %o', (error, title) => {
    render(<ApiErrorState error={error} />)
    expect(screen.getByText(title)).toBeInTheDocument()
  })

  it('retries only transient failures and opens the readiness panel on 409', async () => {
    const onRetry = vi.fn()
    const { unmount } = render(
      <ApiErrorState error={new ApiError(503, 'authz_unavailable', 'x')} onRetry={onRetry} />,
    )
    await userEvent.click(screen.getByRole('button', { name: '다시 시도' }))
    expect(onRetry).toHaveBeenCalled()
    unmount()
    render(<ApiErrorState error={new ApiError(404, 'not_found', 'x')} onRetry={onRetry} />)
    expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  })

  it('opens the check panel for agent_not_ready', async () => {
    const error = new ApiError(409, 'agent_not_ready', 'x', { agent_id: 'a-1' })
    render(<ApiErrorState error={error} />)
    await userEvent.click(screen.getByRole('button', { name: '점검하기' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

function MessageProbe({ error }: { error: unknown }) {
  const message = useApiErrorMessage()
  return <p>{message(error, 'fallback')}</p>
}

describe('useApiErrorMessage', () => {
  it('uses the shared text for known codes and the fallback otherwise', () => {
    const { unmount } = render(<MessageProbe error={new ApiError(503, 'authz_unavailable', 'x')} />)
    expect(screen.getByText(/권한 서버에 일시적인 문제/)).toBeInTheDocument()
    unmount()
    render(<MessageProbe error={new Error('boom')} />)
    expect(screen.getByText('fallback')).toBeInTheDocument()
  })
})
