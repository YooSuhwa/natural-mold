import { render, screen } from '../../test-utils'
import { AssistantPanel } from '@/components/agent/assistant-panel'

// --- Mocks ---

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

vi.mock('@/lib/sse/stream-assistant', () => ({
  streamAssistant: (...args: unknown[]) => mockStreamAssistant(...args),
}))

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
})
