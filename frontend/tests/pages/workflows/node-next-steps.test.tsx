import { NodeSettingsPanel } from '@/features/workflows/components/node-settings-panel'
import type { WorkflowDefinition, WorkflowNode } from '@/lib/types/workflow'
import { render, screen, userEvent } from '../../test-utils'

vi.mock('@/lib/hooks/use-agents', () => ({ useAgents: () => ({ data: [] }) }))

const definition: WorkflowDefinition = {
  schema_version: 'moldy.workflow.v1',
  inputs: [],
  nodes: [
    { id: 'start', type: 'start' },
    { id: 'decide', type: 'decision_bool', label: '긴급한가', condition: { all: [] } },
    { id: 'done', type: 'output', label: '출력', fields: {} },
    { id: 'stop', type: 'fail', label: '실패 종료' },
  ],
  edges: [
    { from: 'start', to: 'decide', when: null },
    { from: 'decide', to: 'done', when: 'true' },
  ],
}

function panel(node: WorkflowNode, onNext = vi.fn()) {
  render(
    <NodeSettingsPanel
      definition={definition}
      node={node}
      labelOf={(id) => definition.nodes.find((item) => item.id === id)?.label ?? id}
      onChange={vi.fn()}
      onInputsChange={vi.fn()}
      onNext={onNext}
      onDelete={vi.fn()}
    />,
  )
  return onNext
}

describe('next steps in the step settings', () => {
  it('shows one choice per exit with the current target, and re-wires or disconnects it', async () => {
    const user = userEvent.setup()
    const onNext = panel(definition.nodes[1])
    const yes = screen.getByLabelText('예일 때')
    const no = screen.getByLabelText('아니오일 때')
    expect(yes).toHaveValue('done')
    expect(no).toHaveValue('')
    // The start node and the decision itself are never offered.
    expect([...(no as HTMLSelectElement).options].map((option) => option.value)).toEqual([
      '',
      'done',
      'stop',
    ])

    await user.selectOptions(no, 'stop')
    expect(onNext).toHaveBeenLastCalledWith('false', 'stop')
    await user.selectOptions(yes, '')
    expect(onNext).toHaveBeenLastCalledWith('true', null)
  })

  it('has no next step for an ending step', () => {
    panel(definition.nodes[2])
    expect(screen.queryByText('다음 단계')).not.toBeInTheDocument()
  })
})
