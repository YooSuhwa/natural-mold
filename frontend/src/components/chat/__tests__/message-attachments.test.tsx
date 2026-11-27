import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, within } from '../../../../tests/test-utils'
import {
  AttachmentModelInputNote,
  MessageAttachmentItem,
  UserMessageAttachments,
  fileItemToBrief,
} from '@/components/chat/message-attachments'
import { ApiError } from '@/lib/api/errors'
import type { FileItem, MessageAttachmentBrief } from '@/lib/types'

vi.mock('@/lib/hooks/use-api-object-url', () => ({
  useApiObjectUrl: (url: string | null) =>
    url ? { status: 'ready', objectUrl: 'blob:test-image' } : { status: 'idle', objectUrl: null },
}))

let visibleMessageId: string | null = 'public-user'

vi.mock('@assistant-ui/react', async () => ({
  ...(await vi.importActual<typeof import('@assistant-ui/react')>('@assistant-ui/react')),
  useAuiState: (
    selector: (state: {
      message: { role: string; id: string; metadata: { custom: Record<string, string> } }
    }) => unknown,
  ) =>
    selector({
      message: {
        role: 'user',
        id: 'runtime-user',
        metadata: {
          custom: visibleMessageId === null ? {} : { publicMessageId: visibleMessageId },
        },
      },
    }),
}))
vi.mock('@/components/chat/conversation-context', () => ({
  useChatConversationId: () => 'conversation',
}))
const textFile: FileItem = {
  source: 'attached',
  id: 'attachment',
  name: 'quoted-report.txt',
  mime_type: 'text/plain',
  preview_url: '/api/uploads/attachment',
  download_url: '/api/uploads/attachment',
  message_id: 'public-user',
  created_at: '2026-11-27T00:00:00Z',
  editable: false,
  size_bytes: 28,
  model_input: 'unsupported',
}
const imageFile: FileItem = {
  ...textFile,
  id: 'photo',
  name: 'photo.png',
  mime_type: 'image/png',
  preview_url: '/api/uploads/photo',
  download_url: '/api/uploads/photo',
}
let queryResult: { data: FileItem[]; error: Error | null } = { data: [textFile], error: null }
vi.mock('@/lib/hooks/use-conversation-files', () => ({
  useConversationFiles: () => queryResult,
}))
afterEach(() => {
  visibleMessageId = 'public-user'
  queryResult = { data: [textFile], error: null }
})

it('renders persisted attachments for a runtime message carrying its public ID', () => {
  render(<UserMessageAttachments />)
  expect(screen.getByRole('button', { name: /quoted-report.txt/ })).toBeInTheDocument()
  expect(screen.getByText('모델에 전달 안 됨')).toBeInTheDocument()
})

it('shows the persisted description with its original image after reopening the chat', async () => {
  const user = userEvent.setup()
  const described: FileItem = {
    ...imageFile,
    model_input: 'image_description',
    image_description: { status: 'ready', text: 'Three green squares <b>in a row</b>.' },
  }
  queryResult.data = [
    described,
    {
      ...described,
      id: 'another',
      message_id: 'another-turn',
      image_description: { status: 'ready', text: 'Hidden turn.' },
    },
  ]
  const first = render(<UserMessageAttachments />)
  expect(screen.getByText('첨부 이미지 설명')).toBeInTheDocument()
  first.unmount()
  render(<UserMessageAttachments />)
  expect(screen.getByText('Three green squares <b>in a row</b>.')).toBeInTheDocument()
  expect(screen.queryByText('Hidden turn.')).toBeNull()
  expect(screen.queryByText('모델에 전달 안 됨')).toBeNull()
  const open = screen.getByRole('button', { name: 'photo.png 파일 열기' })
  open.focus()
  await user.keyboard('{Enter}')
  expect(
    within(await screen.findByRole('dialog')).getByRole('img', { name: 'photo.png' }),
  ).toBeInTheDocument()
})

it('keeps each branch caption on its own message when the same upload is reused', () => {
  queryResult.data = [
    {
      ...imageFile,
      image_description: { status: 'ready', text: 'Wrong branch fallback' },
      image_description_uses: [
        { message_id: 'public-user', image_description: { status: 'ready', text: 'Three shapes' } },
        {
          message_id: 'edited-user',
          image_description: { status: 'ready', text: 'Green and blue' },
        },
      ],
    },
  ]
  const view = render(<UserMessageAttachments />)
  expect(screen.getByText('Three shapes')).toBeInTheDocument()
  expect(screen.queryByText('Green and blue')).toBeNull()
  expect(screen.queryByText('Wrong branch fallback')).toBeNull()

  visibleMessageId = 'edited-user'
  view.rerender(<UserMessageAttachments />)
  expect(screen.getByRole('button', { name: 'photo.png 파일 열기' })).toBeInTheDocument()
  expect(screen.getByText('Green and blue')).toBeInTheDocument()
  expect(screen.queryByText('Three shapes')).toBeNull()

  visibleMessageId = 'unrelated'
  view.rerender(<UserMessageAttachments />)
  expect(screen.queryByRole('img')).toBeNull()
})

it('matches the exact runtime ID on a live message before its public ID is hydrated', () => {
  visibleMessageId = null
  queryResult.data = [
    {
      ...imageFile,
      message_id: 'another-public-id',
      image_description_uses: [
        {
          message_id: 'other-public-id',
          runtime_message_id: 'other-runtime-id',
          image_description: { status: 'ready', text: 'Other question' },
        },
        {
          message_id: 'public-user',
          runtime_message_id: 'runtime-user',
          image_description: { status: 'ready', text: 'Live question' },
        },
      ],
    },
  ]
  const view = render(<UserMessageAttachments />)
  expect(screen.getByText('Live question')).toBeInTheDocument()
  expect(screen.queryByText('Other question')).toBeNull()

  visibleMessageId = 'public-user'
  view.rerender(<UserMessageAttachments />)
  expect(screen.getByText('Live question')).toBeInTheDocument()
})

it('keeps the original attachment without guessing a caption when a branch has no matching use', () => {
  queryResult.data = [
    {
      ...imageFile,
      image_description: { status: 'ready', text: 'Sibling answer' },
      image_description_uses: [
        { message_id: 'sibling', image_description: { status: 'ready', text: 'Sibling answer' } },
      ],
    },
  ]
  render(<UserMessageAttachments />)
  expect(screen.getByRole('img', { name: 'photo.png' })).toBeInTheDocument()
  expect(screen.queryByText('Sibling answer')).toBeNull()
})

it.each([
  ['policy_denied', '이 첨부에 이미지 설명을 사용할 권한이 없습니다.'],
  ['timeout', '이미지 설명 요청 시간이 초과됐습니다.'],
  ['unconfirmed', '실행이 끝나 이미지 설명 요청의 처리 결과를 확인할 수 없습니다.'],
  ['future_reason', '이미지 설명을 불러올 수 없습니다.'],
])('renders only a localized reason for unavailable descriptions (%s)', (reason, message) => {
  queryResult.data = [
    {
      ...imageFile,
      model_input: 'image_no_vision',
      image_description: { status: 'unavailable', reason },
    },
  ]
  render(<UserMessageAttachments />)
  expect(screen.getByText(message)).toBeInTheDocument()
  expect(screen.queryByText(reason)).toBeNull()
  expect(screen.queryByText('모델에 전달 안 됨')).toBeNull()
})

it('removes cached captions after an attachment query loses access', () => {
  queryResult.data = [
    { ...imageFile, image_description: { status: 'ready', text: 'Scoped caption.' } },
  ]
  const view = render(<UserMessageAttachments />)
  expect(screen.getByText('Scoped caption.')).toBeInTheDocument()
  queryResult.error = new ApiError(404, 'NOT_FOUND', '')
  view.rerender(<UserMessageAttachments />)
  expect(screen.queryByText('Scoped caption.')).toBeNull()
  expect(screen.queryByRole('img')).toBeNull()
})

it('keeps the original attachment on a transient refetch failure and hides the cached description', () => {
  queryResult = {
    data: [{ ...imageFile, image_description: { status: 'ready', text: 'Cached caption.' } }],
    error: new TypeError('Network error'),
  }
  render(<UserMessageAttachments />)
  expect(screen.getByRole('img', { name: 'photo.png' })).toBeInTheDocument()
  expect(screen.queryByText('Cached caption.')).toBeNull()
})

vi.mock('@/lib/chat/attachment-preview', () => ({
  getAttachmentTextPreview: vi.fn(async () => ({
    text: 'hello from a text attachment',
    truncated: false,
    mime_type: 'text/plain',
    size_bytes: 28,
  })),
}))

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

  it('renders text content for a text attachment (via /uploads/{id}/content, not artifacts)', async () => {
    const user = userEvent.setup()
    render(
      <MessageAttachmentItem
        brief={brief({ id: 'u4', filename: 'note.txt', mime_type: 'text/plain' })}
      />,
    )

    await user.click(screen.getByRole('button', { name: /note\.txt/ }))
    const dialog = await screen.findByRole('dialog')
    // The body text comes from the upload content endpoint, not an empty 404.
    expect(await within(dialog).findByText(/hello from a text attachment/)).toBeInTheDocument()
  })
})

describe('AttachmentModelInputNote', () => {
  it.each(['image_no_vision', 'unsupported'])('marks %s as not sent to the model', (form) => {
    render(<AttachmentModelInputNote modelInput={form} />)
    expect(screen.getByText('모델에 전달 안 됨')).toBeInTheDocument()
  })

  it.each(['image', 'text', 'pdf', null, undefined, 'image_no'])(
    'renders nothing for %s',
    (form) => {
      const { container } = render(<AttachmentModelInputNote modelInput={form} />)
      expect(container).toBeEmptyDOMElement()
    },
  )
})
