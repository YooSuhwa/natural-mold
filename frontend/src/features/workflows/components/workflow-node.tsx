'use client'

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { EdgeWhen, WorkflowNodeType } from '@/lib/types/workflow'

import { NODE_ICONS } from '../lib/node-catalog'
import type { DiffStatus } from '../lib/version-diff'

export type WorkflowNodeData = {
  type: WorkflowNodeType
  title: string
  subtitle: string
  /** The data contract line (outputs, the rule, the wait); shown when the toggle is on. */
  detail: string
  showDetail: boolean
  issueCount: number
  selected: boolean
  /** Every exit in this draft (a looping decision has a third, ``limit``). */
  exits: (EdgeWhen | null)[]
  freeExits: (EdgeWhen | null)[]
  onAddAfter?: (nodeId: string, when: EdgeWhen | null) => void
  /** Set on the version comparison: added green, removed red, changed yellow. */
  diff?: DiffStatus
}

const DIFF_CARD: Record<Exclude<DiffStatus, 'same'>, string> = {
  added: 'border-2 border-status-success',
  removed: 'border-2 border-dashed border-status-danger opacity-70',
  changed: 'border-2 border-status-warn',
}
const DIFF_TONE: Record<Exclude<DiffStatus, 'same'>, string> = {
  added: 'moldy-status-success',
  removed: 'moldy-status-danger',
  changed: 'moldy-status-warn',
}

export type WorkflowFlowNode = Node<WorkflowNodeData, 'workflow'>

/** The short kind marker on a card (appendix D): IN, A, IF, ✓, FX, OUT, X. */
export const KIND_TAG: Record<WorkflowNodeType, string> = {
  start: 'IN',
  agent: 'A',
  decision_bool: 'IF',
  approval: '✓',
  transform: 'FX',
  output: 'OUT',
  fail: 'X',
}

export function handleId(when: EdgeWhen | null): string {
  return when ?? 'next'
}

/** One node card: icon, title, kind, issue badge, one handle per exit. */
export function WorkflowNodeCard({ id, data }: NodeProps<WorkflowFlowNode>) {
  const t = useTranslations('workflows')
  const Icon = NODE_ICONS[data.type]
  const exits = data.exits
  return (
    <div
      className={cn(
        'moldy-card relative flex h-full w-full items-center gap-3 px-3 py-2',
        data.selected && 'ring-2 ring-primary-strong',
        data.issueCount > 0 && 'border-status-danger',
        data.diff && data.diff !== 'same' && DIFF_CARD[data.diff],
      )}
      data-testid={`workflow-node-${id}`}
    >
      {data.type !== 'start' ? <Handle type="target" position={Position.Top} /> : null}
      <span className="moldy-status-icon flex size-8 shrink-0 flex-col items-center justify-center">
        <Icon className="size-3.5" aria-hidden />
        <span className="text-xs leading-none font-semibold">{KIND_TAG[data.type]}</span>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{data.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{data.subtitle}</span>
        {data.showDetail && data.detail ? (
          <span className="block truncate font-mono text-xs text-muted-foreground">
            {data.detail}
          </span>
        ) : null}
      </span>
      {data.diff && data.diff !== 'same' ? (
        <span
          className={cn(
            'moldy-status-surface rounded-full px-2 py-0.5 shrink-0 text-xs',
            DIFF_TONE[data.diff],
          )}
        >
          {t(`versions.status.${data.diff}`)}
        </span>
      ) : null}
      {data.issueCount > 0 ? (
        <span className="moldy-status-surface moldy-status-danger rounded-full px-2 py-0.5 shrink-0 text-xs">
          {data.issueCount}
        </span>
      ) : null}
      {exits.map((when, index) => (
        <Handle
          key={handleId(when)}
          id={handleId(when)}
          type="source"
          position={Position.Bottom}
          className={cn(
            exits.length === 2 && (index === 0 ? 'left-1/4' : 'left-3/4'),
            exits.length === 3 && ['left-1/6', 'left-1/2', 'left-5/6'][index],
          )}
        />
      ))}
      {exits.length >= 2 ? (
        <span className="pointer-events-none absolute inset-x-0 top-full mt-1 flex justify-around text-xs text-muted-foreground">
          {exits.map((when) => (
            <span key={handleId(when)}>{when ? t(`branch.${when}`) : null}</span>
          ))}
        </span>
      ) : null}
      {data.freeExits.length > 0 ? (
        <span className="absolute inset-x-0 top-full mt-5 flex justify-around">
          {data.freeExits.map((when) => (
            <Button
              key={handleId(when)}
              size="icon"
              variant="outline"
              className="nodrag size-6 rounded-full"
              aria-label={
                when ? `${t('editor.addAfter')} · ${t(`branch.${when}`)}` : t('editor.addAfter')
              }
              onClick={(event) => {
                event.stopPropagation()
                data.onAddAfter?.(id, when)
              }}
            >
              <PlusIcon className="size-3" aria-hidden />
            </Button>
          ))}
        </span>
      ) : null}
    </div>
  )
}
