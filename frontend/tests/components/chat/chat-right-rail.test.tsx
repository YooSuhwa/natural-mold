import { Provider, createStore } from 'jotai'
import { render, waitFor } from '../../test-utils'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'

describe('ChatRightRail', () => {
  it('resets an open panel from a different conversation', async () => {
    const store = createStore()
    store.set(chatRightRailAtom, {
      mode: 'tool-result',
      toolResult: {
        conversationId: 'conversation-old',
        toolCallId: 'tc-1',
        toolName: 'old_tool',
        result: '{"ok":true}',
        status: 'complete',
      },
    })

    render(
      <Provider store={store}>
        <ChatRightRail conversationId="conversation-new" />
      </Provider>,
    )

    await waitFor(() => {
      expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
    })
  })
})
