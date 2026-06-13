export type ChipKind = 'tool' | 'subagent' | 'thinking'
export type ChipStatus = 'loading' | 'success' | 'error' | 'cancelled'

export interface ChipInfo {
  kind: ChipKind
  status: ChipStatus
  title: string
  /** 보조 라벨 — 도구는 결과 길이/개수, plan은 todos 카운트 등. */
  meta?: string | undefined
}

export type OrderedChip = ChipInfo & { order: number }

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const asRecord = (value: unknown): Record<string, unknown> => (isRecord(value) ? value : {})

export const asRecords = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? value.filter(isRecord) : []

export const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value : undefined

export const subagentTitle = (parameters: Record<string, unknown>): string =>
  text(parameters.agent_name) ?? text(parameters.subagent_type) ?? 'Sub-agent'

export function planMeta(parameters: Record<string, unknown>): string | undefined {
  const items = asRecords(Array.isArray(parameters.todos) ? parameters.todos : parameters.items)
  if (items.length === 0) return undefined
  const completed = items.filter((it) => text(it.status) === 'completed').length
  return `${completed}/${items.length}`
}

export const resultMeta = (result: unknown): string | undefined =>
  typeof result === 'string' && result ? `${result.length} chars` : undefined

export function chip(
  kind: ChipKind,
  status: ChipStatus,
  title: string,
  order: number,
  meta?: string,
): OrderedChip {
  return meta ? { kind, status, title, order, meta } : { kind, status, title, order }
}
