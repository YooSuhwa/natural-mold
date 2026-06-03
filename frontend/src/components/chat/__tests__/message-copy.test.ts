import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyTextToClipboard, getMessageCopyText } from '../message-copy'

function setClipboard(clipboard: Pick<Clipboard, 'writeText'> | undefined) {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: clipboard,
  })
}

describe('message copy helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(navigator, 'clipboard')
  })

  it('extracts only copyable text from mixed message content', () => {
    expect(
      getMessageCopyText([
        { type: 'text', text: '사용자 질문' },
        { type: 'tool-call' },
        { type: 'reasoning', text: '중간 추론' },
        { type: 'image' },
      ]),
    ).toBe('사용자 질문\n\n중간 추론')
  })

  it('passes string content through unchanged', () => {
    expect(getMessageCopyText('plain prompt')).toBe('plain prompt')
  })

  it('writes text through the async Clipboard API', async () => {
    const writeText = vi.fn<Clipboard['writeText']>().mockResolvedValue(undefined)
    setClipboard({ writeText })

    await copyTextToClipboard('복사할 메시지')

    expect(writeText).toHaveBeenCalledWith('복사할 메시지')
  })

  it('falls back to document copy command when Clipboard API is unavailable', async () => {
    const execCommand = vi.fn().mockReturnValue(true)
    setClipboard(undefined)
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })

    await copyTextToClipboard('fallback text')

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })
})
