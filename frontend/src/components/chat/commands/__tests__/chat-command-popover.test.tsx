import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ slashOptions: vi.fn() }))

vi.mock('@assistant-ui/react', () => ({
  unstable_useSlashCommandAdapter: (options: {
    commands: readonly { id: string; label: string; description?: string; execute: () => void }[]
  }) => {
    mocks.slashOptions(options)
    return { action: {}, adapter: {} }
  },
  ComposerPrimitive: {
    Unstable_TriggerPopover: Object.assign(
      ({ children }: { children: ReactNode }) => <div>{children}</div>,
      {
        Action: () => null,
      },
    ),
    Unstable_TriggerPopoverItems: ({
      children,
    }: {
      children: (items: readonly { id: string; label: string; description?: string }[]) => ReactNode
    }) => children(mocks.slashOptions.mock.calls.at(-1)?.[0].commands ?? []),
    Unstable_TriggerPopoverItem: ({
      children,
      disabled,
      item,
    }: {
      children: ReactNode
      disabled?: boolean
      item: { execute: () => void }
    }) => (
      <button type="button" disabled={disabled} onClick={item.execute}>
        {children}
      </button>
    ),
  },
}))

import { ChatCommandPopover } from '../chat-command-popover'

describe('ChatCommandPopover', () => {
  it('runs an enabled official trigger item and disables an unavailable command', () => {
    const execute = vi.fn()
    render(
      <ChatCommandPopover
        ariaLabel="Commands"
        commands={[
          {
            id: 'search',
            label: '/search',
            description: 'Search transcript',
            availability: { kind: 'enabled' },
            execute,
            acceptsArgument: true,
          },
          {
            id: 'compact',
            label: '/compact',
            description: 'Compact context',
            availability: { kind: 'disabled', reason: 'No manual compact endpoint' },
            execute: vi.fn(),
            acceptsArgument: false,
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /\/search.*Search transcript/ }))
    expect(execute).toHaveBeenCalledWith('')
    expect(
      screen.getByRole('button', { name: /\/compact.*No manual compact endpoint/ }),
    ).toBeDisabled()
  })
})
