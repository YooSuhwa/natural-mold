import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../../tests/test-utils'
import { JumpToMessageButton, jumpToMessage, messageAnchorExists } from '../jump-to-message'

describe('messageAnchorExists / jumpToMessage', () => {
  let scrollIntoView: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    scrollIntoView = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
  })

  afterEach(() => {
    scrollIntoView.mockRestore()
    document.body.innerHTML = ''
  })

  it('detects whether a message anchor is in the loaded transcript', () => {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-moldy-message-id', 'msg-1')
    document.body.appendChild(anchor)

    expect(messageAnchorExists('msg-1')).toBe(true)
    expect(messageAnchorExists('missing')).toBe(false)
  })

  it('scrolls to + highlights the anchor and returns true when present', () => {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-moldy-message-id', 'msg-2')
    document.body.appendChild(anchor)

    expect(jumpToMessage('msg-2')).toBe(true)
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
    expect(anchor.classList.contains('moldy-jump-highlight')).toBe(true)
  })

  it('returns false and does nothing when the anchor is not loaded', () => {
    expect(jumpToMessage('not-loaded')).toBe(false)
    expect(scrollIntoView).not.toHaveBeenCalled()
  })
})

describe('JumpToMessageButton', () => {
  let scrollIntoView: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    scrollIntoView = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {})
  })

  afterEach(() => {
    scrollIntoView.mockRestore()
    document.body.innerHTML = ''
  })

  it('renders an enabled "대화로 이동" action and scrolls when the message is loaded', async () => {
    render(
      <div>
        <div data-moldy-message-id="loaded-1">target message</div>
        <JumpToMessageButton messageId="loaded-1" />
      </div>,
    )

    const button = await screen.findByRole('button', { name: '대화로 이동' })
    await userEvent.click(button)

    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('renders a disabled "이전 메시지" label when the message is not in the loaded page', () => {
    render(<JumpToMessageButton messageId="elsewhere" />)

    expect(screen.getByText('이전 메시지')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '대화로 이동' })).toBeNull()
  })

  it('renders nothing when there is no message id', () => {
    const { container } = render(<JumpToMessageButton messageId={null} />)

    expect(container).toBeEmptyDOMElement()
  })
})
