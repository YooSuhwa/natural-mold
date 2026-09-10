import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef } from 'react'
import { AssistantRuntimeProvider, ComposerPrimitive, useLocalRuntime } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatConversationContext } from '@/components/chat/conversation-context'
import { useResourceContextComposer } from '@/lib/chat/context/use-resource-context-composer'
import { QuoteComposerBridge } from './quote-composer-bridge'
import { SideChatContext, type SideChatContextValue, type MessageQuote } from './side-chat-context'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

const quote: MessageQuote = {
  kind: 'conversation',
  id: '11111111-1111-4111-8111-111111111111',
  message_id: 'message-1',
  quote: '선택한 문장',
  comment: '댓글',
}
function workspace(): SideChatContextValue {
  return {
    mainId: quote.id,
    mainTitle: '원문',
    sideId: 'side',
    open: true,
    loading: false,
    error: false,
    delivery: null,
    draft: { current: null },
    show: vi.fn(),
    close: vi.fn(),
    deliver: vi.fn(),
    acknowledge: vi.fn(),
  }
}
function Composer({ value }: { value: SideChatContextValue }) {
  const input = useRef<HTMLTextAreaElement>(null)
  const context = useResourceContextComposer(0, value.draft.current?.references)
  return (
    <>
      <QuoteComposerBridge context={context} inputRef={input} />
      <ComposerPrimitive.Input ref={input} aria-label="draft" />
      <button onClick={() => context.add(quote)}>add reference</button>
      <output data-testid="refs">{JSON.stringify(context.references)}</output>
    </>
  )
}
function Harness({ value, run }: { value: SideChatContextValue; run?: (custom: unknown) => void }) {
  const runtime = useLocalRuntime({
    async *run(options) {
      run?.(options.runConfig?.custom)
      yield { content: [{ type: 'text' as const, text: 'Answer' }] }
    },
  })
  return (
    <SideChatContext.Provider value={value}>
      <ChatConversationContext.Provider value="side">
        <AssistantRuntimeProvider runtime={runtime}>
          <Composer value={value} />
        </AssistantRuntimeProvider>
      </ChatConversationContext.Provider>
    </SideChatContext.Provider>
  )
}

describe('quote delivery through official composer', () => {
  it('sends More detail with the quoted context instead of an empty reference payload', async () => {
    const run = vi.fn()
    const value = workspace()
    const view = render(<Harness value={value} run={run} />)
    view.rerender(
      <Harness
        run={run}
        value={{
          ...value,
          delivery: {
            id: 1,
            target: 'side',
            reference: quote,
            prompt: '자세히 설명해 주세요.',
          },
        }}
      />,
    )
    await waitFor(() => expect(run).toHaveBeenCalledWith({ resource_context: [quote] }))
    expect(value.acknowledge).toHaveBeenCalledTimes(1)
  })
  it('does not send or replace an existing side draft when delivering an excerpt', async () => {
    const run = vi.fn()
    const value = workspace()
    const view = render(<Harness value={value} run={run} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'draft' }), {
      target: { value: '기존 초안' },
    })
    view.rerender(
      <Harness
        run={run}
        value={{
          ...value,
          delivery: {
            id: 1,
            target: 'side',
            reference: quote,
            prompt: '설명',
          },
        }}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('refs')).toHaveTextContent('선택한 문장'))
    expect(screen.getByRole('textbox', { name: 'draft' })).toHaveValue('기존 초안')
    expect(run).not.toHaveBeenCalled()
  })
  it('preserves the unsent text and references across responsive runtime remounts', async () => {
    const value = workspace()
    const view = render(<Harness value={value} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'draft' }), {
      target: { value: '보존할 초안' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'add reference' }))
    await waitFor(() => expect(screen.getByTestId('refs')).toHaveTextContent('선택한 문장'))
    view.unmount()
    expect(value.draft.current).toEqual({ text: '보존할 초안', references: [quote] })
    render(<Harness value={value} />)
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'draft' })).toHaveValue('보존할 초안'),
    )
    expect(screen.getByTestId('refs')).toHaveTextContent('선택한 문장')
  })
})
