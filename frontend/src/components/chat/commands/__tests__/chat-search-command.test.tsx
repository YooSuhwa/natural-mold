import { describe, expect, it, vi } from 'vitest'

import { render, screen, waitFor } from '../../../../../tests/test-utils'

vi.mock('@/components/chat/right-rail/jump-to-message', () => ({ jumpToMessage: vi.fn() }))

import { ChatSearchOverlay } from '@/components/chat/chat-search-overlay'

describe('search command action', () => {
  it('applies the local command argument to the existing transcript search', async () => {
    render(
      <>
        <div data-moldy-message-id="message-1">needle in transcript</div>
        <ChatSearchOverlay initialQuery="needle" onClose={() => {}} />
      </>,
    )

    expect(screen.getByRole('textbox', { name: '대화 내 검색' })).toHaveValue('needle')
    await waitFor(() => expect(screen.getByText('1/1')).toBeVisible())
  })
})
