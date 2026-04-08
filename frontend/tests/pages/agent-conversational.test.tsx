import { Suspense, act } from 'react'
import { render, screen, waitFor } from '../test-utils'
import userEvent from '@testing-library/user-event'
import ConversationalCreationPage from '@/app/agents/new/conversational/page'
import type { BuilderSSEEvent } from '@/lib/types'
import { mockBuilderSession, mockAgent } from '../mocks/fixtures'

// --- Mocks ---

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('@/components/chat/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <span>{content}</span>,
}))

const mockBuilderApi = {
  start: vi.fn(),
  getSession: vi.fn(),
  confirm: vi.fn(),
}

vi.mock('@/lib/api/builder', () => ({
  builderApi: {
    start: (...args: unknown[]) => mockBuilderApi.start(...args),
    getSession: (...args: unknown[]) => mockBuilderApi.getSession(...args),
    confirm: (...args: unknown[]) => mockBuilderApi.confirm(...args),
  },
}))

const mockStreamBuilder = vi.fn()

vi.mock('@/lib/sse/stream-builder', () => ({
  streamBuilder: (...args: unknown[]) => mockStreamBuilder(...args),
}))

// Helper: 주어진 이벤트 배열을 yield 하는 async generator
async function* fakeBuilderStream(
  events: BuilderSSEEvent[],
): AsyncGenerator<BuilderSSEEvent> {
  for (const event of events) {
    yield event
  }
}

// use(searchParams)가 Suspense를 필요로 하므로 Suspense boundary 포함
// Promise를 미리 resolve하여 즉시 해소되게 한다
async function renderPage(initialMessage?: string) {
  const searchParams = Promise.resolve(
    initialMessage ? { initialMessage } : {},
  )

  let result: ReturnType<typeof render>
  await act(async () => {
    result = render(
      <Suspense fallback={<div>loading</div>}>
        <ConversationalCreationPage searchParams={searchParams} />
      </Suspense>,
    )
  })
  return result!
}

describe('ConversationalCreationPage (v2)', () => {
  beforeEach(() => {
    mockBuilderApi.start.mockReset()
    mockBuilderApi.getSession.mockReset()
    mockBuilderApi.confirm.mockReset()
    mockStreamBuilder.mockReset()
    // jsdom에는 scrollTo가 없으므로 stub 처리
    Element.prototype.scrollTo = vi.fn()
  })

  it('초기 상태에서 textarea와 빌드 시작 버튼을 표시한다', async () => {
    await renderPage()

    expect(screen.getByText('어떤 에이전트를 만들고 싶으세요?')).toBeInTheDocument()

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"',
    )
    expect(textarea).toBeInTheDocument()

    const startButton = screen.getByRole('button', { name: /빌드 시작/ })
    expect(startButton).toBeInTheDocument()
    expect(startButton).toBeDisabled()
  })

  it('입력 후 빌드 시작 시 builderApi.start를 호출한다', async () => {
    const user = userEvent.setup()

    mockBuilderApi.start.mockResolvedValue({
      ...mockBuilderSession,
      status: 'building',
    })
    mockStreamBuilder.mockReturnValue(fakeBuilderStream([]))
    mockBuilderApi.getSession.mockResolvedValue(mockBuilderSession)

    await renderPage()

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"',
    )
    await user.type(textarea, '뉴스 요약 에이전트')
    await user.click(screen.getByRole('button', { name: /빌드 시작/ }))

    await waitFor(() => {
      expect(mockBuilderApi.start).toHaveBeenCalledWith('뉴스 요약 에이전트')
    })
  })

  it('SSE phase_progress 이벤트로 PhaseTimeline을 업데이트한다', async () => {
    const user = userEvent.setup()

    mockBuilderApi.start.mockResolvedValue({
      ...mockBuilderSession,
      status: 'building',
    })
    mockStreamBuilder.mockReturnValue(
      fakeBuilderStream([
        { event: 'phase_progress', data: { phase: 1, status: 'started' } },
        {
          event: 'phase_progress',
          data: { phase: 1, status: 'completed', message: '초기화 완료' },
        },
        { event: 'phase_progress', data: { phase: 2, status: 'started' } },
        {
          event: 'phase_progress',
          data: { phase: 2, status: 'completed', message: '의도 분석 완료' },
        },
      ]),
    )
    mockBuilderApi.getSession.mockResolvedValue(mockBuilderSession)

    await renderPage()

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"',
    )
    await user.type(textarea, '뉴스 에이전트')
    await user.click(screen.getByRole('button', { name: /빌드 시작/ }))

    // PhaseTimeline이 렌더되어 빌드 진행 상황이 표시된다
    await waitFor(() => {
      expect(screen.getByText('빌드 진행 상황')).toBeInTheDocument()
    })
  })

  it('DraftConfigCard의 확인 버튼으로 builderApi.confirm을 호출한다', async () => {
    const user = userEvent.setup()

    mockBuilderApi.start.mockResolvedValue({
      ...mockBuilderSession,
      id: 'session-confirm',
      status: 'building',
    })
    mockStreamBuilder.mockReturnValue(
      fakeBuilderStream([
        {
          event: 'build_preview',
          data: { draft_config: mockBuilderSession.draft_config },
        },
      ]),
    )
    mockBuilderApi.getSession.mockResolvedValue(mockBuilderSession)
    mockBuilderApi.confirm.mockResolvedValue({ ...mockAgent, id: 'agent-created' })

    await renderPage()

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"',
    )
    await user.type(textarea, '뉴스 에이전트')
    await user.click(screen.getByRole('button', { name: /빌드 시작/ }))

    // DraftConfigCard 표시 대기
    await waitFor(() => {
      expect(screen.getByText('에이전트 구성 완료')).toBeInTheDocument()
    })

    // 에이전트 생성 버튼 클릭
    await user.click(screen.getByRole('button', { name: /에이전트 생성/ }))

    await waitFor(() => {
      expect(mockBuilderApi.confirm).toHaveBeenCalledWith('session-confirm')
    })
  })

  it('빌드 실패 시 에러 메시지를 표시한다', async () => {
    const user = userEvent.setup()

    mockBuilderApi.start.mockResolvedValue({
      ...mockBuilderSession,
      status: 'building',
    })
    mockStreamBuilder.mockReturnValue(
      fakeBuilderStream([
        {
          event: 'build_failed',
          data: { message: '빌드 중 오류 발생' },
        },
      ]),
    )
    // buildStatusRef가 즉시 반영되지 않을 수 있으므로 getSession도 모킹
    mockBuilderApi.getSession.mockResolvedValue({
      ...mockBuilderSession,
      status: 'failed',
    })

    await renderPage()

    const textarea = screen.getByPlaceholderText(
      '예: "한글과컴퓨터 관련 뉴스를 매일 요약해주는 에이전트"',
    )
    await user.type(textarea, '에러 테스트')
    await user.click(screen.getByRole('button', { name: /빌드 시작/ }))

    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toBeInTheDocument()
      expect(screen.getByText('빌드 중 오류 발생')).toBeInTheDocument()
    })
  })
})
