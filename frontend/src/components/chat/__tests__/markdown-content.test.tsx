import { fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { render } from '../../../../tests/test-utils'
import { ChatImage, getChatImagePreviewSrc } from '../markdown-content'

describe('ChatImage', () => {
  it('does not show the loading skeleton again for an image src that already loaded', () => {
    const first = render(<ChatImage src="/api/files/generated.png" alt="guide" />)

    expect(first.container.querySelector('.animate-pulse')).not.toBeNull()

    const img = first.container.querySelector('img')
    expect(img).not.toBeNull()
    fireEvent.load(img as HTMLImageElement)

    expect(first.container.querySelector('.animate-pulse')).toBeNull()
    first.unmount()

    const second = render(<ChatImage src="/api/files/generated.png" alt="guide" />)

    expect(second.container.querySelector('.animate-pulse')).toBeNull()
  })

  it('uses lightweight previews for conversation image files', () => {
    expect(
      getChatImagePreviewSrc(
        'http://localhost:8001/api/conversations/c1/files/generated.png',
      ),
    ).toBe('http://localhost:8001/api/conversations/c1/files/generated.png?variant=preview')
    expect(
      getChatImagePreviewSrc(
        'http://localhost:8001/api/conversations/c1/files/generated.png?download=1',
      ),
    ).toBe(
      'http://localhost:8001/api/conversations/c1/files/generated.png?download=1&variant=preview',
    )
    expect(getChatImagePreviewSrc('data:image/png;base64,abc')).toBe(
      'data:image/png;base64,abc',
    )
  })
})
