import type { ReactNode } from 'react'
import { render, screen } from '../../test-utils'
import { AssistantPanel } from '@/components/agent/assistant-panel'
import { useHiTL } from '@/lib/chat/hitl-context'
import type { Decision, SSEEvent } from '@/lib/types'

// --- Mocks ---

const mockRegisterDecision = vi.fn()
const mockOnResumeDecisions = vi.fn()
const mockSendMessage = vi.fn()
const mockInvalidateQueries = vi.fn()
const mockUseChatRuntime = vi.fn(() => ({
  runtime: { kind: 'assistant-panel-runtime' },
  onResumeDecisions: mockOnResumeDecisions,
  registerDecision: mockRegisterDecision,
  sendMessage: mockSendMessage,
}))

vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-query')>()
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
  }
})

vi.mock('@assistant-ui/react', () => ({
  AssistantRuntimeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  useComposerRuntime: () => ({ setText: vi.fn() }),
}))

vi.mock('@/lib/chat/use-chat-runtime', () => ({
  useChatRuntime: (...args: unknown[]) => mockUseChatRuntime(...args),
}))

vi.mock('@/lib/chat/tool-ui-registry', () => ({
  ALL_TOOL_UI: [{ toolName: 'request_approval' }, { toolName: 'ask_user' }],
}))

vi.mock('@/components/chat/assistant-thread', () => ({
  AssistantThread: ({
    emptyContent,
    toolUI,
  }: {
    emptyContent: ReactNode
    toolUI: readonly { toolName?: string }[]
  }) => {
    const hitl = useHiTL()
    return (
      <div
        data-has-register-decision={String(typeof hitl?.registerDecision === 'function')}
        data-testid="assistant-thread"
        data-tool-names={toolUI.map((ui) => ui.toolName).join(',')}
      >
        {emptyContent}
      </div>
    )
  },
}))

vi.mock('@/components/chat/markdown-content', () => ({
  MarkdownContent: ({ content }: { content: string }) => <span>{content}</span>,
}))

vi.mock('@/components/chat/markdown-components', () => ({
  // 테스트 자체는 markdown 렌더 검증 안 함 — 빈 객체 stub.
  buildMarkdownComponents: () => ({}),
}))

vi.mock('@/components/chat/chat-image', () => ({
  ChatImage: () => null,
}))

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

const mockStreamAssistant = vi.fn()
const mockStreamAssistantResume = vi.fn()

vi.mock('@/lib/sse/stream-assistant', () => ({
  streamAssistant: (...args: unknown[]) => mockStreamAssistant(...args),
  streamAssistantResume: (...args: unknown[]) => mockStreamAssistantResume(...args),
}))

type ResumeFn = (
  decisions: Decision[],
  signal: AbortSignal,
  displayText?: string,
  interruptId?: string | null,
) => AsyncGenerator<SSEEvent>

type ChatRuntimeOptions = {
  resumeFn?: ResumeFn
  onStreamEnd?: (didMutate: boolean) => void
}

describe('AssistantPanel', () => {
  beforeEach(() => {
    mockStreamAssistant.mockReset()
    mockRegisterDecision.mockReset()
    mockOnResumeDecisions.mockReset()
    mockSendMessage.mockReset()
    mockUseChatRuntime.mockClear()
    mockStreamAssistantResume.mockReset()
    mockInvalidateQueries.mockReset()
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

  it('쓰기 도구 승인 UI와 HiTL resume 컨텍스트를 AssistantThread에 제공한다', () => {
    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const thread = screen.getByTestId('assistant-thread')
    expect(thread).toHaveAttribute('data-tool-names', 'request_approval,ask_user')
    expect(thread).toHaveAttribute('data-has-register-decision', 'true')
  })

  it('승인 결정을 Assistant resume SSE로 재개한다', async () => {
    const decision: Decision = { type: 'approve' }
    mockStreamAssistantResume.mockImplementation(async function* (): AsyncGenerator<SSEEvent> {
      yield { event: 'message_end', data: { content: 'done' } } as SSEEvent
    })

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const options = mockUseChatRuntime.mock.calls[0]?.[0] as ChatRuntimeOptions
    const resumeFn = options.resumeFn
    expect(resumeFn).toBeTypeOf('function')
    if (!resumeFn) throw new Error('Assistant resume function should be registered')

    const signal = new AbortController().signal
    for await (const _event of resumeFn([decision], signal, '승인됨', 'intr-1')) {
      void _event
    }

    expect(mockStreamAssistantResume).toHaveBeenCalledWith(
      'agent-1',
      [decision],
      signal,
      '승인됨',
      'intr-1',
      expect.any(String),
    )
  })

  it('승인 재개가 끝나면 에이전트 설정 캐시를 갱신한다', async () => {
    mockStreamAssistantResume.mockImplementation(async function* (): AsyncGenerator<SSEEvent> {
      yield { event: 'message_end', data: { content: 'done' } } as SSEEvent
    })

    render(<AssistantPanel agentId="agent-1" agentName="Test Agent" />)

    const options = mockUseChatRuntime.mock.calls[0]?.[0] as ChatRuntimeOptions
    const resumeFn = options.resumeFn
    if (!resumeFn) throw new Error('Assistant resume function should be registered')
    const signal = new AbortController().signal
    for await (const _event of resumeFn([{ type: 'approve' }], signal)) {
      void _event
    }
    options.onStreamEnd?.(false)

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['agents'] })
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['agents', 'agent-1'] })
  })
})
