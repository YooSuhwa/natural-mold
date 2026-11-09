'use client'

import { BaseEdge, EdgeLabelRenderer, type Edge, type EdgeProps } from '@xyflow/react'
import { useTranslations } from 'next-intl'

export type WorkflowLoopEdgeData = {
  /** The x of the side lane: right of every step in the loop. */
  laneX: number
  maxRounds: number | null
}

export type WorkflowLoopFlowEdge = Edge<WorkflowLoopEdgeData, 'loop'>

const TURN = 18

/**
 * A loop's back-edge (workflow-loop-plan 3.4): down out of the decision's exit, out to a lane
 * beside the loop, up, and into the top of the step it goes back to. Dashed, with the round
 * limit on the lane.
 */
export function WorkflowLoopEdge(props: EdgeProps<WorkflowLoopFlowEdge>) {
  const t = useTranslations('workflows.loop')
  const lane = props.data?.laneX ?? Math.max(props.sourceX, props.targetX) + 160
  const top = props.targetY - TURN
  const bottom = props.sourceY + TURN
  const path = [
    `M ${props.sourceX},${props.sourceY}`,
    `L ${props.sourceX},${bottom}`,
    `L ${lane},${bottom}`,
    `L ${lane},${top}`,
    `L ${props.targetX},${top}`,
    `L ${props.targetX},${props.targetY}`,
  ].join(' ')
  const rounds = props.data?.maxRounds
  const labelY = (top + bottom) / 2
  return (
    <>
      <BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} />
      <EdgeLabelRenderer>
        <div
          className="moldy-status-surface moldy-status-info nodrag nopan pointer-events-none absolute rounded-full px-2 py-0.5 text-xs whitespace-nowrap"
          style={{ transform: `translate(-50%, -50%) translate(${lane}px, ${labelY}px)` }}
        >
          {rounds ? t('badge', { count: rounds }) : t('badgeUnset')}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
