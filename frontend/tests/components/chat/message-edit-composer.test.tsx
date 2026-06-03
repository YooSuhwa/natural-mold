import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { render } from '../../test-utils'

const mocks = vi.hoisted(() => ({
  cancel: vi.fn(),
  send: vi.fn(),
  setText: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({
    message: () => ({
      composer: () => ({
        cancel: mocks.cancel,
        getState: () => ({ canCancel: true, isEditing: true, isEmpty: false }),
        send: mocks.send,
        setText: mocks.setText,
      }),
    }),
    thread: () => ({
      getState: () => ({ capabilities: { attachments: false, queue: false }, isRunning: false }),
    }),
  }),
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({
      message: {
        composer: {
          canCancel: true,
          dictation: null,
          isEditing: true,
          isEmpty: false,
          text: 'edited message',
        },
      },
      thread: { isDisabled: false },
    }),
}))

import { MessageEditComposerRoot } from '@/components/chat/message-edit-composer'

describe('MessageEditComposerRoot', () => {
  it('returns focus to the thread composer after saving an edit', async () => {
    render(
      <>
        <textarea aria-label="Thread composer" data-moldy-composer-input="true" />
        <MessageEditComposerRoot>
          <button type="submit">Save</button>
        </MessageEditComposerRoot>
      </>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(mocks.send).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(screen.getByLabelText('Thread composer')).toHaveFocus()
    })
  })
})
