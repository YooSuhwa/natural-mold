import type { WorkflowDefinition } from '@/lib/types/workflow'

import { contractLine } from './schema-rows'

export interface NodeTitle {
  title: string
  subtitle: string
  /** The data contract line (outputs, the rule, the wait). */
  detail: string
}

/**
 * The card text for a node: shared by the editor and the version comparison, so a node
 * reads the same in both. ``t`` is the ``workflows`` translator.
 */
export function nodeTitle(
  definition: WorkflowDefinition,
  nodeId: string,
  agentNames: ReadonlyMap<string, string>,
  t: (key: string, values?: Record<string, string | number>) => string,
): NodeTitle {
  const node = definition.nodes.find((item) => item.id === nodeId)
  if (!node) return { title: nodeId, subtitle: '', detail: '' }
  const kind = t(`kind.${node.type}`)
  if (node.type === 'agent' || node.type === 'for_each') {
    const agentName = agentNames.get(node.agent_id) ?? t('settings.pickAgent')
    const fields = contractLine(node.output_schema)
    const each =
      node.type === 'for_each'
        ? t('detail.forEach', { source: `${node.source}${node.path.replaceAll('/', '.')}` })
        : ''
    const output = fields ? t('detail.output', { fields }) : ''
    return {
      title: node.label || (node.type === 'agent' ? agentName : kind),
      subtitle: node.label ? agentName : node.type === 'agent' ? kind : agentName,
      detail: [each, output].filter(Boolean).join(' · '),
    }
  }
  let detail = ''
  if (node.type === 'start') detail = definition.inputs.map((item) => item.name).join(', ')
  if (node.type === 'decision_bool' && node.evaluator === 'llm') {
    detail = t('settings.evaluatorKind.llm')
  } else if (node.type === 'decision_bool' && node.condition) {
    const clauses = node.condition.all ?? node.condition.any ?? []
    const first = clauses[0]
    detail = first
      ? `${first.source}${first.path.replaceAll('/', '.')} ${t(`settings.op.${first.op}`)}${
          first.op === 'exists' ? '' : ` ${String(first.value ?? '')}`
        }${clauses.length > 1 ? ` +${clauses.length - 1}` : ''} · ${t('settings.evaluatorKind.rule')}`
      : ''
  }
  if ((node.type === 'approval' || node.type === 'ask') && node.timeout_minutes) {
    detail = t('detail.timeout', { minutes: node.timeout_minutes })
  }
  if (node.type === 'switch') {
    detail = `${node.source}${node.path.replaceAll('/', '.')} → ${node.cases
      .map((item) => item.name)
      .join(', ')}`
  }
  if (node.type === 'llm') detail = contractLine(node.output_schema)
  if (node.type === 'parallel') {
    detail = node.branches
      .map((branch) => `${branch.name}: ${agentNames.get(branch.agent_id) ?? '?'}`)
      .join(', ')
  }
  if (node.type === 'tool') detail = t(`settings.toolName.${node.tool}`)
  if (node.type === 'transform' || node.type === 'output') {
    detail = Object.keys(node.fields ?? {}).join(', ')
  }
  if (node.type === 'fail') detail = node.reason ?? ''
  return { title: node.label || kind, subtitle: node.label ? kind : '', detail }
}
