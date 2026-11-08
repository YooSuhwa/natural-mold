'use client'

import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
} from '@xyflow/react'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import type { WorkflowEdge } from '@/lib/types/workflow'

export type WorkflowEdgeData = {
  edge: WorkflowEdge
  onInsert: (edge: WorkflowEdge) => void
}

export type WorkflowFlowEdge = Edge<WorkflowEdgeData, 'workflow'>

/** A connection with a "+" in the middle that inserts a node on it. */
export function WorkflowEdgeLine(props: EdgeProps<WorkflowFlowEdge>) {
  const t = useTranslations('workflows')
  const [path, labelX, labelY] = getSmoothStepPath(props)
  const data = props.data
  return (
    <>
      <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} />
      {data ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-auto absolute"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            <Button
              size="icon"
              variant="outline"
              className="size-6 rounded-full"
              aria-label={t('editor.insertHere')}
              onClick={() => data.onInsert(data.edge)}
            >
              <PlusIcon className="size-3" aria-hidden />
            </Button>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  )
}
