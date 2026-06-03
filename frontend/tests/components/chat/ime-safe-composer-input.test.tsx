import { fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../test-utils'

const mocks = vi.hoisted(() => ({
  addAttachment: vi.fn(),
  composerText: '',
  send: vi.fn(),
  setText: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({
    composer: () => ({
      addAttachment: mocks.addAttachment,
      getState: () => ({ isEditing: true, isEmpty: false }),
      send: mocks.send,
      setText: mocks.setText,
    }),
    thread: () => ({
      getState: () => ({ capabilities: { attachments: false, queue: false }, isRunning: false }),
    }),
  }),
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({
      composer: { dictation: null, isEditing: true, text: mocks.composerText },
      thread: { isDisabled: false },
    }),
}))

import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'

describe('ImeSafeComposerInput', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.composerText = ''
  })

  it('focuses the composer input when auto focus is requested', () => {
    render(<ImeSafeComposerInput autoFocus placeholder="메시지 입력..." />)

    expect(screen.getByPlaceholderText('메시지 입력...')).toHaveFocus()
  })

  it('restores focus when submitted composer text is cleared', async () => {
    mocks.composerText = 'hello'
    const { rerender } = render(<ImeSafeComposerInput placeholder="메시지 입력..." />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    expect(textarea).not.toHaveFocus()

    mocks.composerText = ''
    rerender(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    await waitFor(() => {
      expect(textarea).toHaveFocus()
    })
  })

  it('keeps IME composition local until the syllable is committed', () => {
    render(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    const textarea = screen.getByPlaceholderText('메시지 입력...')

    fireEvent.compositionStart(textarea)
    fireEvent.change(textarea, { target: { value: 'ㅎ' } })
    fireEvent.change(textarea, { target: { value: '한' } })

    expect(mocks.setText).not.toHaveBeenCalled()

    fireEvent.compositionEnd(textarea)

    expect(mocks.setText).toHaveBeenCalledWith('한')
  })

  it('syncs ordinary changes immediately', () => {
    render(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    fireEvent.change(screen.getByPlaceholderText('메시지 입력...'), {
      target: { value: 'hello' },
    })

    expect(mocks.setText).toHaveBeenCalledWith('hello')
  })
})
