'use client'

import { use, useState, useEffect, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import {
  ArrowLeftIcon,
  ClipboardListIcon,
  Loader2Icon,
  Trash2Icon,
  WorkflowIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { useTriggers, useDeleteTrigger } from '@/lib/hooks/use-triggers'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { FormMode } from './_components/form-mode/form-mode'
import { RightPanel, type RightTab } from './_components/right-panel/right-panel'
import { useAgentSettingsDraft } from './_hooks/use-agent-settings-draft'

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
  const tc = useTranslations('common')
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: middlewares } = useMiddlewares()
  const { data: triggers } = useTriggers(agentId)
  const updateAgent = useUpdateAgent(agentId)
  const deleteAgent = useDeleteAgent()
  const deleteTrigger = useDeleteTrigger(agentId)
  const { draft, actions: draftActions, isDirty, updateRequest } = useAgentSettingsDraft(agent)

  const [leftTab, setLeftTab] = useState<LeftTab>('form')
  const [rightTab, setRightTab] = useState<RightTab>('fix')
  const [initialFixMessage, setInitialFixMessage] = useState<string | undefined>()
  const [justCreated, setJustCreated] = useState(false)
  const [deleteAgentConfirmOpen, setDeleteAgentConfirmOpen] = useState(false)
  const [deletingTriggerTarget, setDeletingTriggerTarget] = useState<{
    id: string
    description: string
  } | null>(null)

  const visualControlledState = useMemo(
    () => ({
      name: draft.name,
      description: draft.description,
      systemPrompt: draft.systemPrompt,
      modelId: draft.modelId,
      identityMode: draft.identityMode,
      temperature: draft.temperature,
      topP: draft.topP,
      maxTokens: draft.maxTokens,
      selectedToolIds: draft.selectedToolIds,
      selectedMcpToolIds: draft.selectedMcpToolIds,
      selectedSkillIds: draft.selectedSkillIds,
      selectedSubAgentIds: draft.selectedSubAgentIds,
      selectedMiddlewareTypes: draft.selectedMiddlewareTypes,
    }),
    [draft],
  )

  const visualControlledHandlers = useMemo(
    () => ({
      onNameChange: draftActions.setName,
      onDescriptionChange: draftActions.setDescription,
      onSystemPromptChange: draftActions.setSystemPrompt,
      onModelIdChange: draftActions.setModelId,
      onIdentityModeChange: draftActions.setIdentityMode,
      onTemperatureChange: draftActions.setTemperature,
      onTopPChange: draftActions.setTopP,
      onMaxTokensChange: draftActions.setMaxTokens,
      onToggleTool: draftActions.toggleTool,
      onToggleMcpTool: draftActions.toggleMcpTool,
      onToggleSkill: draftActions.toggleSkill,
      onToggleSubAgent: draftActions.toggleSubAgent,
      onToggleMiddleware: draftActions.toggleMiddleware,
    }),
    [draftActions],
  )

  // 만들기 페이지에서 carry된 첫 메시지 검사 → Fix 탭 활성 + 자동 전송 + create-hero 유지
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const msg = sessionStorage.getItem('fix-initial-message')
    if (msg) {
      sessionStorage.removeItem('fix-initial-message')
      setInitialFixMessage(msg)
      setRightTab('fix')
      setJustCreated(true)
    }
  }, [])
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  async function handleSave() {
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

  function handleBack() {
    if (isDirty && !window.confirm(t('unsavedWarning'))) return
    const hasAppHistory =
      typeof document !== 'undefined' &&
      document.referrer &&
      new URL(document.referrer).origin === window.location.origin
    if (hasAppHistory) {
      router.back()
    } else {
      router.push(`/agents/${agentId}`)
    }
  }

  if (agentLoading) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
        <Skeleton className="h-6 w-40" />
        <div className="space-y-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    )
  }

  return (
    <div className="moldy-app-surface flex flex-1 flex-col overflow-hidden">
      <header className="moldy-panel-header flex items-start gap-3 px-6 py-3">
        <Button variant="ghost" size="icon-sm" onClick={handleBack} aria-label={t('back')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <AgentAvatar imageUrl={agent?.image_url ?? null} name={draft.name} size="sm" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Input
            value={draft.name}
            onChange={(e) => draftActions.setName(e.target.value)}
            className="h-8 rounded-md border-0 bg-transparent px-2 text-lg font-semibold shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
            placeholder={t('namePlaceholder')}
          />
          <Input
            value={draft.description}
            onChange={(e) => draftActions.setDescription(e.target.value)}
            className="h-7 rounded-md border-0 bg-transparent px-2 text-xs text-muted-foreground shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
            placeholder={t('descriptionPlaceholder')}
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('deleteAgent')}
            onClick={() => setDeleteAgentConfirmOpen(true)}
          >
            <Trash2Icon className="size-4 text-destructive" />
          </Button>
          <Button onClick={handleSave} disabled={updateAgent.isPending || !isDirty}>
            {updateAgent.isPending ? <Loader2Icon className="mr-1 size-4 animate-spin" /> : null}
            {t('save')}
          </Button>
        </div>
      </header>

      <main className="grid flex-1 grid-cols-1 gap-3 overflow-hidden p-3 lg:grid-cols-2">
        <section className="moldy-panel flex min-h-0 flex-col overflow-hidden">
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
              <FormMode
                systemPrompt={draft.systemPrompt}
                onSystemPromptChange={draftActions.setSystemPrompt}
                selectedSubAgentIds={draft.selectedSubAgentIds}
                onToggleSubAgent={draftActions.toggleSubAgent}
                currentAgentId={agentId}
                modelId={draft.modelId}
                onModelIdChange={draftActions.setModelId}
                temperature={draft.temperature}
                onTemperatureChange={draftActions.setTemperature}
                topP={draft.topP}
                onTopPChange={draftActions.setTopP}
                maxTokens={draft.maxTokens}
                onMaxTokensChange={draftActions.setMaxTokens}
                onResetModelParams={draftActions.resetModelParams}
                fallbackIds={draft.fallbackIds}
                onFallbackIdsChange={draftActions.setFallbackIds}
                selectedToolIds={draft.selectedToolIds}
                onToggleTool={draftActions.toggleTool}
                selectedMcpToolIds={draft.selectedMcpToolIds}
                onToggleMcpTool={draftActions.toggleMcpTool}
                selectedSkillIds={draft.selectedSkillIds}
                onToggleSkill={draftActions.toggleSkill}
                selectedMiddlewareTypes={draft.selectedMiddlewareTypes}
                onToggleMiddleware={draftActions.toggleMiddleware}
              />
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

        <section className="moldy-panel flex min-h-0 flex-col overflow-hidden">
          <RightPanel
            tab={rightTab}
            onTabChange={setRightTab}
            agentId={agentId}
            agentName={agent?.name ?? ''}
            agentImageUrl={agent?.image_url ?? null}
            identityMode={draft.identityMode}
            onIdentityModeChange={draftActions.setIdentityMode}
            openerQuestions={draft.openerQuestions}
            onOpenerQuestionsChange={draftActions.setOpenerQuestions}
            onRequestDeleteTrigger={setDeletingTriggerTarget}
            initialFixMessage={initialFixMessage}
            createMode={justCreated}
          />
        </section>
      </main>

      <DeleteConfirmDialog
        open={deleteAgentConfirmOpen}
        onOpenChange={setDeleteAgentConfirmOpen}
        title={t('deleteDialog.title')}
        description={t('deleteDialog.description')}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deleteAgent.isPending}
        onConfirm={handleDelete}
      />

      {/* Trigger Delete Confirm */}
      <DeleteConfirmDialog
        open={!!deletingTriggerTarget}
        onOpenChange={(v) => !v && setDeletingTriggerTarget(null)}
        title={t('trigger.deleteConfirm')}
        description={deletingTriggerTarget?.description ?? ''}
        cancelLabel={tc('cancel')}
        confirmLabel={tc('delete')}
        isPending={deleteTrigger.isPending}
        onConfirm={() => {
          if (deletingTriggerTarget) {
            deleteTrigger.mutate(deletingTriggerTarget.id)
            setDeletingTriggerTarget(null)
          }
        }}
      />
    </div>
  )
}
