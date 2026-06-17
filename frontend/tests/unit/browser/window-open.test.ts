import { afterEach, describe, expect, it, vi } from 'vitest'

import { openExternalUrl, openNamedPopupWindow } from '@/lib/browser/window-open'

describe('window-open helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('opens safe URLs with noopener and noreferrer by default', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)

    openExternalUrl('/files/report.pdf')

    expect(open).toHaveBeenCalledWith(
      new URL('/files/report.pdf', window.location.origin).href,
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('blocks unsafe URL protocols', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)

    expect(openExternalUrl('javascript:alert(1)')).toBeNull()

    expect(open).not.toHaveBeenCalled()
  })

  it('opens named popup windows through the explicit popup helper', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)

    openNamedPopupWindow('moldy-oauth', 'popup,width=560,height=760')

    expect(open).toHaveBeenCalledWith('about:blank', 'moldy-oauth', 'popup,width=560,height=760')
  })
})
