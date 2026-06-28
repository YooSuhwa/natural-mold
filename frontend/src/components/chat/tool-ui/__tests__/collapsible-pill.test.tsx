import type { ReactElement } from 'react'
import { fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { CollapsiblePill } from '../collapsible-pill'

const BODY = 'scoped pill body'

function pill(defaultExpanded: boolean): ReactElement {
  return (
    <CollapsiblePill status="success" title="agent_demo" defaultExpanded={defaultExpanded}>
      <span>{BODY}</span>
    </CollapsiblePill>
  )
}

describe('CollapsiblePill expansion', () => {
  it('renders the body only when mounted expanded', () => {
    render(pill(true))
    expect(screen.getByText(BODY)).toBeInTheDocument()
  })

  it('hides the body when mounted collapsed', () => {
    render(pill(false))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
  })

  it('auto-expands when defaultExpanded flips false→true after mount (reload hydration)', () => {
    // A subagent card mounts collapsed (snapshot not yet seeded), then its
    // discovery snapshot lands and defaultExpanded flips true.
    const { rerender } = render(pill(false))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
    rerender(pill(true))
    expect(screen.getByText(BODY)).toBeInTheDocument()
  })

  it('does not re-open a user collapse when defaultExpanded later oscillates false→true', () => {
    const { rerender } = render(pill(false))
    rerender(pill(true)) // hydration → auto-expands
    expect(screen.getByText(BODY)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Collapse')) // user collapses
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()

    // Snapshot drops then re-seeds across runs: defaultExpanded false→true again.
    rerender(pill(false))
    rerender(pill(true))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
  })

  it('keeps a user collapse on a card mounted expanded across a later oscillation', () => {
    const { rerender } = render(pill(true))
    fireEvent.click(screen.getByLabelText('Collapse'))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()

    rerender(pill(false))
    rerender(pill(true))
    expect(screen.queryByText(BODY)).not.toBeInTheDocument()
  })
})
