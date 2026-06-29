import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { TerminalCard } from '../terminal-card'

describe('TerminalCard', () => {
  it('renders array lines joined, with command and exit code header', () => {
    render(<TerminalCard command="pytest -q" exitCode={0} lines={['line one', 'line two']} />)
    expect(screen.getByTestId('data-ui-terminal')).toBeInTheDocument()
    expect(screen.getByText('$ pytest -q')).toBeInTheDocument()
    expect(screen.getByText('exit 0')).toBeInTheDocument()
    expect(screen.getByText(/line one/)).toBeInTheDocument()
    expect(screen.getByText(/line two/)).toBeInTheDocument()
  })

  it('renders a string body and omits the header when no command/exit code', () => {
    render(<TerminalCard lines="just output" />)
    expect(screen.getByText('just output')).toBeInTheDocument()
    expect(screen.queryByText(/^exit/)).not.toBeInTheDocument()
  })

  it('shows a non-zero exit code', () => {
    render(<TerminalCard command="build" exitCode={1} lines="boom" />)
    expect(screen.getByText('exit 1')).toBeInTheDocument()
  })
})
