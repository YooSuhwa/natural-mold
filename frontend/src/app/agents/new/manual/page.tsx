'use client'

import { ReactFlowProvider } from '@xyflow/react'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { Skeleton } from '@/components/ui/skeleton'
import { VisualSettingsFlow } from '@/components/agent/visual-settings/visual-settings-flow'

export default function ManualCreationPage() {
  const { data: models, isLoading: modelsLoading } = useModels()
  const { data: tools, isLoading: toolsLoading } = useTools()
  const { data: skills, isLoading: skillsLoading } = useSkills()
  const { data: middlewares } = useMiddlewares()

  if (modelsLoading || toolsLoading || skillsLoading || !models?.length) {
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
          models={models}
          tools={tools ?? []}
          skills={skills ?? []}
          middlewares={middlewares ?? []}
          mode="create"
        />
      </ReactFlowProvider>
    </div>
  )
}
