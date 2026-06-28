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
  it('renders an image attachment via the shared ChatImage viewer', () => {
    render(<MessageAttachmentItem brief={brief()} />)

    const thumbnail = screen.getByRole('img', { name: 'photo.png' })
    // 마크다운/인라인 이미지와 동일한 공용 ChatImage(.chat-image)로 렌더된다.
    expect(thumbnail).toHaveClass('chat-image')
  })

  it('opens a fullscreen image lightbox when the image is clicked', async () => {
    const user = userEvent.setup()
    render(<MessageAttachmentItem brief={brief()} />)

    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getByRole('img', { name: 'photo.png' }))

    // ChatImage 라이트박스(DialogShell) 안에 원본 이미지가 뜬다.
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('img', { name: 'photo.png' })).toBeInTheDocument()
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

  it('opens the artifact preview dialog for a non-image file', async () => {
    const user = userEvent.setup()
    render(
      <MessageAttachmentItem
        brief={brief({ id: 'u3', filename: 'spec.pdf', mime_type: 'application/pdf' })}
      />,
    )

    expect(screen.queryByRole('dialog')).toBeNull()
    await user.click(screen.getByRole('button', { name: /spec\.pdf/ }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })
})
