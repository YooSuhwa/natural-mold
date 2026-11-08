import { describe, expect, it } from 'vitest'

import { inquiryTemplate } from '../templates'

const t = (key: string) => `<${key}>`

describe('inquiryTemplate (Q3 고객 문의 답변 초안)', () => {
  const definition = inquiryTemplate({ classifier: 'agent-a', writer: 'agent-b' }, t)

  it('uses all seven node kinds with the chosen agents', () => {
    expect(new Set(definition.nodes.map((node) => node.type))).toEqual(
      new Set(['start', 'agent', 'decision_bool', 'approval', 'transform', 'output', 'fail']),
    )
    const agents = definition.nodes.flatMap((node) =>
      node.type === 'agent' ? [[node.id, node.agent_id]] : [],
    )
    expect(agents).toEqual([
      ['classify', 'agent-a'],
      ['draft', 'agent-b'],
    ])
  })

  it('wires both exits of the decision and the approval', () => {
    const exits = (from: string) =>
      definition.edges.filter((edge) => edge.from === from).map((edge) => [edge.when, edge.to])
    expect(exits('is_urgent')).toEqual([
      ['true', 'review'],
      ['false', 'draft'],
    ])
    expect(exits('review')).toEqual([
      ['approved', 'draft'],
      ['rejected', 'rejected'],
    ])
  })

  it('takes every visible text from the translator', () => {
    const labels = definition.nodes.flatMap((node) => (node.label ? [node.label] : []))
    expect(labels.every((label) => /^<.+>$/.test(label))).toBe(true)
    expect(definition.inputs[0].label).toBe('<inputLabel>')
  })
})
