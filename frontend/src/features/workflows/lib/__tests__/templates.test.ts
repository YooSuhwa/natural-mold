import { describe, expect, it } from 'vitest'

import { loopsOf } from '../loops'
import { buildTemplate, inquiryTemplate, rewriteTemplate } from '../templates'

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

describe('rewriteTemplate (통과할 때까지 다시 쓰기)', () => {
  const definition = rewriteTemplate({ writer: 'agent-w', reviewer: 'agent-r' }, t)

  it('loops a failed review back to the draft, three rounds, then asks the runner', () => {
    const [loop] = loopsOf(definition)
    expect(loop).toMatchObject({ decision: 'passed', when: 'false', target: 'draft' })
    expect([...loop.body].sort()).toEqual(['draft', 'passed', 'review'])
    const decision = definition.nodes.find((node) => node.id === 'passed')
    expect(decision).toMatchObject({ repeat: { max_rounds: 3 } })
    expect(definition.edges).toContainEqual({ from: 'passed', to: 'escalate', when: 'limit' })
  })

  it("feeds the previous round's review into the rewrite", () => {
    const draft = definition.nodes.find((node) => node.id === 'draft')
    expect(draft?.type === 'agent' && draft.prompt).toContainEqual({
      ref: { node: 'review', path: '/feedback', previous: true },
    })
  })

  it('is built from the picked agents by role', () => {
    const built = buildTemplate('rewrite', { writer: 'w', reviewer: 'r' }, t)
    const agents = built?.nodes.flatMap((node) => (node.type === 'agent' ? [node.agent_id] : []))
    expect(agents).toEqual(['w', 'r'])
    expect(buildTemplate('blank', {}, t)).toBeUndefined()
  })
})
