'use client'

import { use } from 'react'
import dynamic from 'next/dynamic'
import { useAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { useTriggers } from '@/lib/hooks/use-triggers'
import { Skeleton } from '@/components/ui/skeleton'
import { useAgentAccess } from '@/components/agent/agent-access-context'
import { useTranslations } from 'next-intl'

function VisualFlowLoading() {
  return (
    <div className="flex flex-1 flex-col gap-4 p-6">
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-[calc(100vh-10rem)] w-full" />
    </div>
  )
}

const VisualSettingsIsland = dynamic(
  () =>
    import('@/components/agent/visual-settings/visual-settings-island').then(
      (mod) => mod.VisualSettingsIsland,
    ),
  {
    ssr: false,
    loading: () => <VisualFlowLoading />,
  },
)

const EMPTY_RESOURCE_LIST: never[] = []

export default function VisualSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: middlewares } = useMiddlewares()
  const { data: triggers } = useTriggers(agentId)
  const { canEdit } = useAgentAccess()
  const tc = useTranslations('common')

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
      {canEdit ? null : (
        <p role="note" className="px-6 pt-3 text-xs text-muted-foreground">
          {tc('readOnly')}
        </p>
      )}
      {/* Plan 13.9 row 2: without can_edit the canvas is view-only. */}
      <fieldset disabled={!canEdit} className="flex min-h-0 flex-1 flex-col">
        <VisualSettingsIsland
          agent={agent}
          agentId={agentId}
          models={models ?? EMPTY_RESOURCE_LIST}
          tools={tools ?? EMPTY_RESOURCE_LIST}
          skills={skills ?? EMPTY_RESOURCE_LIST}
          middlewares={middlewares ?? EMPTY_RESOURCE_LIST}
          triggers={triggers ?? EMPTY_RESOURCE_LIST}
        />
      </fieldset>
    </div>
  )
}
