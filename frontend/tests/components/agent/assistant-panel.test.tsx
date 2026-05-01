import { render, screen, waitFor } from '../../test-utils'
import userEvent from '@testing-library/user-event'
import { AssistantPanel } from '@/components/agent/assistant-panel'
import type { SSEEvent } from '@/lib/types'

// --- Mocks ---

vi.mock('@/components/chat/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <span>{content}</span>,
}))

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

const mockStreamAssistant = vi.fn()

vi.mock('@/lib/sse/stream-assistant', () => ({
  streamAssistant: (...args: unknown[]) => mockStreamAssistant(...args),
}))

// Helper: 주어진 이벤트 배열을 yield 하는 async generator
async function* fakeStream(events: SSEEvent[]): AsyncGenerator<SSEEvent> {
  for (const event of events) {
    yield event
  }
}

describe('AssistantPanel', () => {
  beforeEach(() => {
    mockStreamAssistant.mockReset()
    // jsdom에는 scrollTo가 없으므로 stub 처리
    Element.prototype.scrollTo = vi.fn()
  })

  // assistant-ui의 ThreadEmptyMessage는 jsdom에서 thread state 초기화가 동기적으로
  // 끝나지 않아 emptyContent가 비결정적으로 렌더된다. hero title까지만 검증.
  it('초기 렌더 시 hero title을 표시한다', () => {
    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    // EmptyContent의 FixHero가 ``fixHeroTitle({ agentName })`` 키 ("{agentName} 수정")로 렌더.
    expect(screen.getByText('Test Agent 수정')).toBeInTheDocument()
  })

  // AssistantPanel은 @assistant-ui/react 통합으로 재작성되어 placeholder/textarea
  // 셀렉터 기반 단위 테스트가 무의미. e2e/smoke.spec.ts와 manual QA로 대체.
  it.skip('메시지 입력 후 Enter로 streamAssistant를 호출한다', async () => {
    const user = userEvent.setup()
    mockStreamAssistant.mockReturnValue(
      fakeStream([
        {
          event: 'content_delta',
          data: { delta: 'Hello!' },
        } as SSEEvent,
        {
          event: 'message_end',
          data: { content: 'Hello!', usage: {} },
        } as SSEEvent,
      ]),
    )

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const textarea = screen.getByPlaceholderText('수정 요청을 입력하세요...')
    await user.type(textarea, '존댓말로 변경해줘')
    await user.keyboard('{Enter}')

    await waitFor(() => {
      expect(mockStreamAssistant).toHaveBeenCalledWith(
        'agent-1',
        '존댓말로 변경해줘',
        expect.any(AbortSignal),
        expect.any(String),
      )
    })
  })

  it.skip('전송 중 로딩 상태를 표시한다', async () => {
    const user = userEvent.setup()

    // 무한 대기하는 스트림으로 로딩 상태 유지
    let resolveStream: () => void = () => {}
    mockStreamAssistant.mockReturnValue(
      (async function* () {
        await new Promise<void>((resolve) => {
          resolveStream = resolve
        })
      })(),
    )

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const textarea = screen.getByPlaceholderText('수정 요청을 입력하세요...')
    await user.type(textarea, '테스트')
    await user.click(screen.getByRole('button', { name: '전송' }))

    await waitFor(() => {
      const sendButton = screen.getByRole('button', { name: '전송' })
      expect(sendButton).toBeDisabled()
    })

    resolveStream()
  })

  it.skip('에러 메시지를 role="alert"로 표시한다', async () => {
    const user = userEvent.setup()
    mockStreamAssistant.mockReturnValue(
      fakeStream([
        {
          event: 'error',
          data: { message: '서버 오류 발생' },
        } as SSEEvent,
      ]),
    )

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const textarea = screen.getByPlaceholderText('수정 요청을 입력하세요...')
    await user.type(textarea, '에러 테스트')
    await user.keyboard('{Enter}')

    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toBeInTheDocument()
    })
  })

  it.skip('제안 버튼 클릭 시 입력 필드에 텍스트를 채운다', async () => {
    const user = userEvent.setup()

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    await user.click(screen.getByText('존댓말로 바꿔줘'))

    const textarea = screen.getByPlaceholderText('수정 요청을 입력하세요...')
    expect(textarea).toHaveValue('존댓말로 바꿔줘')
  })
})
