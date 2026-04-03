'use client'

import { use } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { useAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useTriggers } from '@/lib/hooks/use-triggers'
import { Skeleton } from '@/components/ui/skeleton'
import { VisualSettingsFlow } from '@/components/agent/visual-settings/visual-settings-flow'

export default function VisualSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: triggers } = useTriggers(agentId)

  if (agentLoading || !agent) {
    return (
      <div className="flex flex-1 flex-col gap-4 p-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-[calc(100vh-10rem)] w-full" />
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <ReactFlowProvider>
        <VisualSettingsFlow
          agent={agent}
          agentId={agentId}
          models={models ?? []}
          tools={tools ?? []}
          skills={skills ?? []}
          triggers={triggers ?? []}
        />
      </ReactFlowProvider>
    </div>
  )
}
