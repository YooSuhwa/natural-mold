import { describe, expect, it } from 'vitest'
import { render, screen, userEvent, within } from '../../../../tests/test-utils'
import { MessageAttachmentItem, fileItemToBrief } from '@/components/chat/message-attachments'
import type { FileItem, MessageAttachmentBrief } from '@/lib/types'

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

describe('fileItemToBrief', () => {
  it('maps an attached /files row to a preview brief (preview_url → url)', () => {
    const file: FileItem = {
      source: 'attached',
      id: 'upload-9',
      name: 'report.pdf',
      mime_type: 'application/pdf',
      extension: 'pdf',
      kind: null,
      size_bytes: 42,
      preview_url: '/api/uploads/upload-9',
      download_url: '/api/uploads/upload-9',
      message_id: 'msg-1',
      created_at: '2026-06-05T00:00:00',
      editable: false,
    }

    expect(fileItemToBrief(file)).toEqual({
      id: 'upload-9',
      filename: 'report.pdf',
      mime_type: 'application/pdf',
      size_bytes: 42,
      url: '/api/uploads/upload-9',
    })
  })

  it('defaults a missing size to 0', () => {
    const file = { id: 'u', name: 'f', mime_type: 'image/png', preview_url: '/x' } as FileItem
    expect(fileItemToBrief(file).size_bytes).toBe(0)
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
