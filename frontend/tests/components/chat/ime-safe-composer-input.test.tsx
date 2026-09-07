import { fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../test-utils'

const mocks = vi.hoisted(() => ({
  addAttachment: vi.fn(),
  composerText: '',
  hasQueue: false,
  isRunning: false,
  send: vi.fn(),
  setText: vi.fn(),
}))

vi.mock('@assistant-ui/react', () => ({
  unstable_useTriggerPopoverAriaProps: () => ({}),
  unstable_useTriggerPopoverRootContextOptional: () => null,
  useAui: () => ({
    composer: {
      addAttachment: mocks.addAttachment,
      getState: () => ({ isEditing: true, isEmpty: false, text: mocks.composerText }),
      send: mocks.send,
      setText: mocks.setText,
    },
    thread: {
      getState: () => ({
        capabilities: { attachments: false, queue: mocks.hasQueue },
        isRunning: mocks.isRunning,
      }),
    },
  }),
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({
      composer: { dictation: null, isEditing: true, runConfig: {}, text: mocks.composerText },
      thread: { isDisabled: false },
    }),
}))

import { ImeSafeComposerInput } from '@/components/chat/ime-safe-composer-input'

describe('ImeSafeComposerInput', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.composerText = ''
    mocks.hasQueue = false
    mocks.isRunning = false
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

  it('inserts an IME syllable before a final dictated transcript that arrives during composition', () => {
    mocks.composerText = '초안'
    const { rerender } = render(<ImeSafeComposerInput placeholder="메시지 입력..." />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    textarea.setSelectionRange(2, 2)
    fireEvent.compositionStart(textarea)
    fireEvent.change(textarea, { target: { value: '초안한' } })

    mocks.composerText = '초안 음성'
    rerender(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    fireEvent.compositionEnd(textarea)

    expect(mocks.setText).toHaveBeenLastCalledWith('초안한 음성')
  })

  it('preserves a selected-text IME replacement when dictation updates during composition', () => {
    mocks.composerText = '첫 초안 문장'
    const { rerender } = render(<ImeSafeComposerInput placeholder="메시지 입력..." />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    textarea.setSelectionRange(2, 4)
    fireEvent.compositionStart(textarea)
    fireEvent.change(textarea, { target: { value: '첫 대체 문장' } })

    mocks.composerText = '첫 초안 문장 음성'
    rerender(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    fireEvent.compositionEnd(textarea)

    expect(mocks.setText).toHaveBeenLastCalledWith('첫 대체 문장 음성')
  })

  it('syncs ordinary changes immediately', () => {
    render(<ImeSafeComposerInput placeholder="메시지 입력..." />)

    fireEvent.change(screen.getByPlaceholderText('메시지 입력...'), {
      target: { value: 'hello' },
    })

    expect(mocks.setText).toHaveBeenCalledWith('hello')
  })

  it('marks ordinary Enter as non-steering when the server queue is available', () => {
    mocks.hasQueue = true
    mocks.isRunning = true
    render(<ImeSafeComposerInput submitMode="enter" placeholder="메시지 입력..." />)

    fireEvent.keyDown(screen.getByPlaceholderText('메시지 입력...'), { key: 'Enter' })

    expect(mocks.send).toHaveBeenCalledExactlyOnceWith({ steer: false })
  })

  it('uses explicit steer only for Shift plus Ctrl Enter', () => {
    mocks.hasQueue = true
    mocks.isRunning = true
    render(<ImeSafeComposerInput submitMode="enter" placeholder="메시지 입력..." />)

    fireEvent.keyDown(screen.getByPlaceholderText('메시지 입력...'), {
      key: 'Enter',
      shiftKey: true,
      ctrlKey: true,
    })

    expect(mocks.send).toHaveBeenCalledExactlyOnceWith({ steer: true })
  })
})
