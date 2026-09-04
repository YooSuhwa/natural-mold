import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  EditFileToolUI,
  isFilesystemPermissionDenied,
  isWriteFileUnavailable,
  shouldFileToolDefaultExpand,
} from '../code-tool-ui'

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    key === 'permissionDenied' ? 'Localized permission denial' : key,
}))

interface EditToolUi {
  readonly render: (props: {
    readonly args: {
      readonly file_path?: string
      readonly path?: string
      readonly old_string?: string
      readonly new_string?: string
    }
    readonly result: unknown
    readonly status: { readonly type: string }
  }) => ReactNode
}

function isEditToolUi(value: unknown): value is EditToolUi {
  return (
    typeof value === 'object' &&
    value !== null &&
    'render' in value &&
    typeof value.render === 'function'
  )
}

function editToolUi(): EditToolUi {
  const candidate: unknown = EditFileToolUI
  if (!isEditToolUi(candidate)) {
    throw new Error('EditFileToolUI test fixture did not expose a render function')
  }
  return candidate
}

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

  it('expands a read_file error when it has a safe user-facing preview', () => {
    expect(
      shouldFileToolDefaultExpand({
        label: 'Read',
        status: 'error',
        hasPreview: true,
      }),
    ).toBe(true)
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

  it('recognizes only the exact runtime filesystem permission denial contract', () => {
    expect(isFilesystemPermissionDenied('Error: filesystem permission denied')).toBe(true)
    expect(isFilesystemPermissionDenied('Error: filesystem permission denied for /runtime/x')).toBe(
      false,
    )
    expect(isFilesystemPermissionDenied('Previous error: filesystem permission denied')).toBe(false)
    expect(isFilesystemPermissionDenied('error: filesystem permission denied')).toBe(false)
    expect(isFilesystemPermissionDenied('wrote /conversations/thread/report.md')).toBe(false)
  })

  it('renders a localized denied edit state without rendering the proposed diff or raw error', () => {
    const proposedOldContent = 'before the denied edit'
    const proposedNewContent = 'after the denied edit'
    const rawDeniedResult = 'Error: filesystem permission denied'

    render(
      editToolUi().render({
        args: {
          file_path: '/conversations/thread-1/blocked.md',
          old_string: proposedOldContent,
          new_string: proposedNewContent,
        },
        result: rawDeniedResult,
        status: { type: 'complete' },
      }),
    )

    expect(screen.getByTestId('filesystem-edit-denied')).toHaveTextContent(
      'Localized permission denial',
    )
    expect(screen.queryByText(proposedOldContent)).not.toBeInTheDocument()
    expect(screen.queryByText(proposedNewContent)).not.toBeInTheDocument()
    expect(screen.queryByText(rawDeniedResult)).not.toBeInTheDocument()
  })

  it('recognizes only bounded write-file unavailability results', () => {
    expect(isWriteFileUnavailable('Error: write_file is not a valid tool')).toBe(true)
    expect(isWriteFileUnavailable('Error: read_file is not a valid tool')).toBe(false)
    expect(isWriteFileUnavailable('wrote /conversations/thread/report.md')).toBe(false)
  })
})
