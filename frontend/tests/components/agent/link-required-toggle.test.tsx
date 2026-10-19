import { AgentLinkRequiredProvider } from '@/components/agent/link-required-context'
import { CurrentColumn } from '@/components/agent/visual-settings/dialogs/tools-skills-current-column'
import type { ToolInstance } from '@/lib/types/tool'
import { render, screen, userEvent } from '../../test-utils'

const tool = { id: 'tool-1', name: 'Slack 전송', definition_key: 'slack' } as unknown as ToolInstance

function column() {
  return (
    <CurrentColumn
      total={1}
      tools={[tool]}
      mcpTools={[]}
      skills={[]}
      onRemoveTool={vi.fn()}
      onRemoveMcp={vi.fn()}
      onRemoveSkill={vi.fn()}
    />
  )
}

describe('S29 required checkbox on agent links', () => {
  it('toggles the required mark of a selected link in the agent editor', async () => {
    const onToggle = vi.fn()
    render(
      <AgentLinkRequiredProvider required={new Set(['tool-1'])} onToggle={onToggle}>
        {column()}
      </AgentLinkRequiredProvider>,
    )
    const checkbox = screen.getByRole('checkbox', { name: 'Slack 전송 필수' })
    expect(checkbox).toBeChecked()
    await userEvent.click(checkbox)
    expect(onToggle).toHaveBeenCalledWith('tool-1')
  })

  it('shows no checkbox outside the agent editor (creation)', () => {
    render(column())
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})
