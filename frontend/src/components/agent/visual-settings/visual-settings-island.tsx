'use client'

import { ReactFlowProvider } from '@xyflow/react'

import {
  VisualSettingsFlow,
  type VisualSettingsFlowProps,
} from '@/components/agent/visual-settings/visual-settings-flow'

export function VisualSettingsIsland(props: VisualSettingsFlowProps) {
  return (
    <ReactFlowProvider>
      <VisualSettingsFlow {...props} />
    </ReactFlowProvider>
  )
}
