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

describe('loops in the step settings', () => {
  const loopDefinition: WorkflowDefinition = {
    schema_version: 'moldy.workflow.v1',
    inputs: [],
    nodes: [
      { id: 'start', type: 'start' },
      { id: 'draft', type: 'transform', label: '초안', template: [] },
      {
        id: 'decide',
        type: 'decision_bool',
        label: '통과?',
        condition: { all: [] },
        repeat: { max_rounds: 3 },
      },
      { id: 'done', type: 'output', label: '출력', fields: {} },
      { id: 'stop', type: 'fail', label: '실패 종료' },
    ],
    edges: [
      { from: 'start', to: 'draft', when: null },
      { from: 'draft', to: 'decide', when: null },
      { from: 'decide', to: 'done', when: 'true' },
      { from: 'decide', to: 'draft', when: 'false' },
    ],
  }

  it('offers going back on yes/no, a limit exit, and the round limit', async () => {
    const user = userEvent.setup()
    const onNext = vi.fn()
    const onChange = vi.fn()
    render(
      <NodeSettingsPanel
        definition={loopDefinition}
        node={loopDefinition.nodes[2]}
        labelOf={(id) => loopDefinition.nodes.find((item) => item.id === id)?.label ?? id}
        onChange={onChange}
        onInputsChange={vi.fn()}
        onNext={onNext}
        onDelete={vi.fn()}
      />,
    )
    const no = screen.getByLabelText('아니오일 때')
    expect(no).toHaveValue('draft')
    expect([...(no as HTMLSelectElement).options].map((option) => option.text)).toContain(
      '↺ 초안(으)로 돌아가기',
    )
    await user.selectOptions(screen.getByLabelText('한도를 넘으면'), 'stop')
    expect(onNext).toHaveBeenLastCalledWith('limit', 'stop')

    const rounds = screen.getByLabelText('최대 횟수')
    expect(rounds).toHaveValue(3)
    await user.clear(rounds)
    await user.type(rounds, '50')
    await user.tab()
    // Clamped to the server's cap.
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'decide', repeat: { max_rounds: 10 } }),
    )
  })
})
