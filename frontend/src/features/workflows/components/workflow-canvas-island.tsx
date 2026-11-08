'use client'

import { ReactFlowProvider } from '@xyflow/react'

import { WorkflowCanvas, type CanvasProps } from './workflow-canvas'

/** The lazily loaded canvas boundary: React Flow stays out of the route bundle. */
export function WorkflowCanvasIsland(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvas {...props} />
    </ReactFlowProvider>
  )
}
