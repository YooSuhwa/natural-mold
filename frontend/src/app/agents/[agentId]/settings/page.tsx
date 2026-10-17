'use client'

import { use, useState, useEffect, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import { ClipboardListIcon, WorkflowIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { consumeFixInitialMessage } from '@/lib/agents/fix-message-handoff'
import { isRuntimePolicyCompatibleWithContextWindow } from '@/lib/agents/runtime-policy-validation'
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { useTriggers, useDeleteTrigger } from '@/lib/hooks/use-triggers'
import { ErrorState } from '@/components/shared/error-state'
import { SettingsFormMode } from './_components/settings-form-mode'
import { RightPanel, type RightTab } from './_components/right-panel/right-panel'
import {
  SettingsConfirmDialogs,
  type TriggerDeleteTarget,
} from './_components/settings-confirm-dialogs'
import { SettingsHeader, SettingsLoading } from './_components/settings-header'
import { useSettingsLeaveGuard } from './_hooks/use-settings-leave-guard'
import { useVisualControlled } from './_hooks/use-visual-controlled'
import { useAgentSettingsDraft } from './_hooks/use-agent-settings-draft'
import { useAgentAccess } from '@/components/agent/agent-access-context'

type LeftTab = 'form' | 'visual'

function VisualFlowLoading() {
  return (
    <div className="flex h-full min-h-[420px] flex-col gap-3 p-4">
      <Skeleton className="h-9 w-48" />
      <Skeleton className="moldy-skeleton-card min-h-0 flex-1" />
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

export default function AgentSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const router = useRouter()
  const t = useTranslations('agent.settings')
  const {
    data: agent,
    isLoading: agentLoading,
    isError: agentLoadError,
    refetch: refetchAgent,
  } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: middlewares } = useMiddlewares()
  const { data: triggers } = useTriggers(agentId)
  const updateAgent = useUpdateAgent(agentId)
  const deleteAgent = useDeleteAgent()
  const deleteTrigger = useDeleteTrigger(agentId)
  const { draft, actions: draftActions, isDirty, updateRequest } = useAgentSettingsDraft(agent)

  const selectedModelContextWindow = useMemo(() => {
    const catalogModel = models?.find((model) => model.id === draft.modelId)
    if (catalogModel) return catalogModel.context_window
    if (agent?.model?.id === draft.modelId) return agent.model.context_window ?? null
    return null
  }, [agent?.model, draft.modelId, models])
  const runtimePolicyCompatible = isRuntimePolicyCompatibleWithContextWindow(
    draft.runtimePolicy,
    selectedModelContextWindow,
  )

  const [leftTab, setLeftTab] = useState<LeftTab>('form')
  const access = useAgentAccess()
  const [rightTab, setRightTab] = useState<RightTab>(access.canEdit ? 'fix' : 'test')
  const [initialFixMessage, setInitialFixMessage] = useState<string | undefined>()
  const [justCreated, setJustCreated] = useState(false)
  const [deleteAgentConfirmOpen, setDeleteAgentConfirmOpen] = useState(false)
  const [deletingTriggerTarget, setDeletingTriggerTarget] = useState<TriggerDeleteTarget | null>(
    null,
  )

  const { state: visualControlledState, handlers: visualControlledHandlers } = useVisualControlled(
    draft,
    draftActions,
  )

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const msg = consumeFixInitialMessage()
    if (msg) {
      setInitialFixMessage(msg)
      setRightTab(access.canEdit ? 'fix' : 'test')
      setJustCreated(true)
    }
  }, [access.canEdit])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleBack = useSettingsLeaveGuard(agentId, isDirty)

  async function handleSave() {
    if (!runtimePolicyCompatible) {
      toast.error(t('runtimePolicy.summarization.invalidCurrent'))
      return
    }
    try {
      await updateAgent.mutateAsync(updateRequest)
      toast.success(t('toast.saved'))
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agentId)
      router.push('/')
    } catch {
      toast.error(t('toast.deleteFailed'))
    }
  }

  if (agentLoading) return <SettingsLoading />

  if (agentLoadError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <ErrorState description={t('loadUnavailable')} onRetry={() => void refetchAgent()} />
      </div>
    )
  }

  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-hidden">
      <SettingsHeader
        agentId={agentId}
        agentName={agent?.name ?? ''}
        imageUrl={agent?.image_url ?? null}
        myPermissions={agent?.my_permissions}
        name={draft.name}
        onNameChange={draftActions.setName}
        description={draft.description}
        onDescriptionChange={draftActions.setDescription}
        onBack={handleBack}
        onRequestDelete={() => setDeleteAgentConfirmOpen(true)}
        onSave={handleSave}
        saving={updateAgent.isPending}
        saveDisabled={updateAgent.isPending || !isDirty || !runtimePolicyCompatible}
      />

      <div className="grid flex-1 grid-cols-1 content-start gap-3 overflow-x-hidden overflow-y-auto p-3 lg:grid-cols-2 lg:content-stretch lg:overflow-hidden">
        <section className="moldy-panel flex min-h-176 min-w-0 flex-col overflow-hidden lg:min-h-0">
          <Tabs
            value={leftTab}
            onValueChange={(v) => setLeftTab(v as LeftTab)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="sticky top-0 z-10 flex justify-center overflow-hidden border-b border-border/60 bg-card/55">
              <TabsList variant="line" className="h-auto">
                <TabsTrigger
                  value="form"
                  className="gap-1 px-4 py-2.5 after:bg-primary-strong data-active:text-primary-strong"
                >
                  <ClipboardListIcon className="size-3.5" />
                  {t('tabs.form')}
                </TabsTrigger>
                <TabsTrigger
                  value="visual"
                  className="gap-1 px-4 py-2.5 after:bg-primary-strong data-active:text-primary-strong"
                >
                  <WorkflowIcon className="size-3.5" />
                  {t('tabs.visual')}
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="form" className="flex flex-1 min-h-0 flex-col overflow-hidden">
              <SettingsFormMode agentId={agentId} draft={draft} actions={draftActions} />
            </TabsContent>
            <TabsContent
              value="visual"
              className="flex flex-1 min-h-0 flex-col overflow-hidden p-0"
            >
              {leftTab === 'visual' && agent ? (
                <VisualSettingsIsland
                  agent={agent}
                  agentId={agentId}
                  models={models ?? EMPTY_RESOURCE_LIST}
                  tools={tools ?? EMPTY_RESOURCE_LIST}
                  skills={skills ?? EMPTY_RESOURCE_LIST}
                  middlewares={middlewares ?? EMPTY_RESOURCE_LIST}
                  triggers={triggers ?? EMPTY_RESOURCE_LIST}
                  embedded
                  controlledState={visualControlledState}
                  controlledHandlers={visualControlledHandlers}
                />
              ) : null}
            </TabsContent>
          </Tabs>
        </section>

        <section className="moldy-panel flex min-h-176 min-w-0 flex-col overflow-hidden lg:min-h-0">
          <RightPanel
            tab={rightTab}
            onTabChange={setRightTab}
            agentId={agentId}
            agentName={agent?.name ?? ''}
            agentImageUrl={agent?.image_url ?? null}
            identityMode={draft.identityMode}
            onIdentityModeChange={draftActions.setIdentityMode}
            runtimePolicy={draft.runtimePolicy}
            onRuntimePolicyChange={draftActions.setRuntimePolicy}
            modelContextWindow={selectedModelContextWindow}
            openerQuestions={draft.openerQuestions}
            onOpenerQuestionsChange={draftActions.setOpenerQuestions}
            onRequestDeleteTrigger={setDeletingTriggerTarget}
            initialFixMessage={initialFixMessage}
            createMode={justCreated}
          />
        </section>
      </div>

      <SettingsConfirmDialogs
        deleteAgentOpen={deleteAgentConfirmOpen}
        onDeleteAgentOpenChange={setDeleteAgentConfirmOpen}
        deletingAgent={deleteAgent.isPending}
        onConfirmDeleteAgent={handleDelete}
        triggerTarget={deletingTriggerTarget}
        onTriggerTargetChange={setDeletingTriggerTarget}
        deletingTrigger={deleteTrigger.isPending}
        onConfirmDeleteTrigger={(id) => deleteTrigger.mutate(id)}
      />
    </div>
  )
}
