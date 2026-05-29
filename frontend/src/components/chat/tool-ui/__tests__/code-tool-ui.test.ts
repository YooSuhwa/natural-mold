import { describe, expect, it } from 'vitest'

import { shouldFileToolDefaultExpand } from '../code-tool-ui'

describe('code tool UI expansion policy', () => {
  it('keeps read_file results collapsed by default', () => {
    expect(
      shouldFileToolDefaultExpand({
        label: 'Read',
        status: 'success',
        hasPreview: true,
      }),
    ).toBe(false)
  })

  it('keeps write/edit previews expanded by default', () => {
    expect(
      shouldFileToolDefaultExpand({
        label: 'Write',
        status: 'success',
        hasPreview: true,
      }),
    ).toBe(true)
    expect(
      shouldFileToolDefaultExpand({
        label: 'Edit',
        status: 'success',
        hasPreview: true,
      }),
    ).toBe(true)
  })
})
