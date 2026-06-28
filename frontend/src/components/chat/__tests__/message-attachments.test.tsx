import { describe, expect, it } from 'vitest'
import { render, screen, userEvent, within } from '../../../../tests/test-utils'
import { MessageAttachmentItem, briefFromAttachment } from '@/components/chat/message-attachments'
import type { MessageAttachmentBrief } from '@/lib/types'

function brief(overrides: Partial<MessageAttachmentBrief> = {}): MessageAttachmentBrief {
  return {
    id: 'upload-1',
    filename: 'photo.png',
    mime_type: 'image/png',
    size_bytes: 1234,
    url: '/api/uploads/upload-1',
    ...overrides,
  }
}

describe('briefFromAttachment', () => {
  it('reconstructs a brief from an image attachment (image content part)', () => {
    const result = briefFromAttachment({
      id: 'upload-1',
      type: 'image',
      name: 'photo.png',
      contentType: 'image/png',
      status: { type: 'complete' },
      content: [{ type: 'image', image: '/api/uploads/upload-1' }],
    })

    expect(result).toEqual({
      id: 'upload-1',
      filename: 'photo.png',
      mime_type: 'image/png',
      size_bytes: 0,
      url: '/api/uploads/upload-1',
    })
  })

  it('prefers the carried url + size_bytes fields when present', () => {
    const result = briefFromAttachment({
      id: 'upload-2',
      type: 'file',
      name: 'report.pdf',
      contentType: 'application/pdf',
      content: [{ type: 'text', text: '[attachment: report.pdf](/api/uploads/upload-2)' }],
      url: '/api/uploads/upload-2',
      size_bytes: 42,
    })

    expect(result).toEqual({
      id: 'upload-2',
      filename: 'report.pdf',
      mime_type: 'application/pdf',
      size_bytes: 42,
      url: '/api/uploads/upload-2',
    })
  })

  it('returns null when there is no recoverable id or url', () => {
    expect(briefFromAttachment(null)).toBeNull()
    expect(briefFromAttachment({ name: 'no-id.png' })).toBeNull()
    expect(
      briefFromAttachment({ id: 'x', name: 'no-url.txt', content: [{ type: 'text' }] }),
    ).toBeNull()
  })
})

describe('MessageAttachmentItem', () => {
  it('renders an image attachment as a clickable thumbnail', () => {
    render(<MessageAttachmentItem brief={brief()} />)

    const button = screen.getByRole('button', { name: /photo\.png/ })
    expect(button).toBeInTheDocument()
    const thumbnail = within(button).getByRole('img')
    expect(thumbnail).toHaveAttribute('alt', 'photo.png')
  })

  it('renders a non-image attachment as a file chip with the filename', () => {
    render(
      <MessageAttachmentItem
        brief={brief({ id: 'u2', filename: 'spec.pdf', mime_type: 'application/pdf' })}
      />,
    )

    const button = screen.getByRole('button', { name: /spec\.pdf/ })
    expect(button).toBeInTheDocument()
    expect(within(button).queryByRole('img')).toBeNull()
    expect(button).toHaveTextContent('spec.pdf')
  })

  it('opens the artifact preview dialog when clicked', async () => {
    const user = userEvent.setup()
    render(<MessageAttachmentItem brief={brief()} />)

    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getByRole('button', { name: /photo\.png/ }))

    const dialog = await screen.findByRole('dialog')
    // 이미지 첨부 → 기존 ImagePreview가 다이얼로그 안에서 미리보기를 렌더한다.
    expect(within(dialog).getByText('이미지 미리보기')).toBeInTheDocument()
    expect(within(dialog).getAllByRole('img').length).toBeGreaterThan(0)
  })
})
