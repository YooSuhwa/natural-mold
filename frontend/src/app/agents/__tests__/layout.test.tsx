import type { ReactNode } from 'react'
import { render, screen } from '../../../../tests/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AgentsLayout from '../layout'

vi.mock('@/i18n/scoped-messages', () => ({
  ScopedIntlProvider({
    children,
    namespaces,
  }: {
    readonly children: ReactNode
    readonly namespaces: readonly string[]
  }) {
    return (
      <div data-testid="scoped-provider" data-namespaces={namespaces.join(',')}>
        {children}
      </div>
    )
  },
}))

describe('AgentsLayout', () => {
  it('includes skill messages when agent dialogs render skill quality badges', async () => {
    const layout = await AgentsLayout({ children: <span>agent settings</span> })

    render(layout)

    expect(screen.getByText('agent settings')).toBeInTheDocument()
    expect(screen.getByTestId('scoped-provider')).toHaveAttribute(
      'data-namespaces',
      expect.stringContaining('skill'),
    )
  })
})
