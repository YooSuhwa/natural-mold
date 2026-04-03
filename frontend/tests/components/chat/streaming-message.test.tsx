import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { Provider, createStore } from 'jotai'
import { StreamingMessage } from '@/components/chat/streaming-message'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
} from '@/lib/stores/chat-store'
import messages from '../../../messages/ko.json'

function renderWithJotai(initialValues?: {
  isStreaming?: boolean
  message?: { id: string; content: string } | null
  toolCalls?: Array<{
    name: string
    status: 'calling' | 'completed'
    params?: Record<string, unknown>
    result?: string
  }>
}) {
  const store = createStore()
  store.set(isStreamingAtom, initialValues?.isStreaming ?? false)
  store.set(streamingMessageAtom, initialValues?.message ?? null)
  store.set(streamingToolCallsAtom, initialValues?.toolCalls ?? [])

  return render(
    <NextIntlClientProvider locale="ko" messages={messages}>
      <Provider store={store}>
        <StreamingMessage />
      </Provider>
    </NextIntlClientProvider>,
  )
}

describe('StreamingMessage', () => {
  it('returns null when not streaming', () => {
    const { container } = renderWithJotai({ isStreaming: false })
    expect(container.innerHTML).toBe('')
  })

  it('shows loading spinner when streaming with no content', () => {
    renderWithJotai({ isStreaming: true, message: null })
    // The thinking dots should be present with "생각 중..." text
    expect(screen.getByText('생각 중...')).toBeInTheDocument()
  })

  it('renders streaming message content', () => {
    renderWithJotai({
      isStreaming: true,
      message: { id: 'msg-1', content: 'Hello streaming' },
    })
    expect(screen.getByText('Hello streaming')).toBeInTheDocument()
  })

  it('renders streaming tool calls', () => {
    renderWithJotai({
      isStreaming: true,
      message: null,
      toolCalls: [{ name: 'Web Search', status: 'calling', params: { query: 'test' } }],
    })
    expect(screen.getByText('Web Search')).toBeInTheDocument()
    expect(screen.getByText('호출 중...')).toBeInTheDocument()
  })

  it('renders completed tool calls with results', () => {
    renderWithJotai({
      isStreaming: true,
      message: null,
      toolCalls: [{ name: 'Web Search', status: 'completed', result: 'Found results' }],
    })
    expect(screen.getByText('완료')).toBeInTheDocument()
    expect(screen.getByText('Found results')).toBeInTheDocument()
  })
})
