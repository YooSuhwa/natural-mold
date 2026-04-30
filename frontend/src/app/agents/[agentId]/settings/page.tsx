'use client'

import { use, useState, useEffect, useMemo, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeftIcon, ClipboardListIcon, Loader2Icon, Trash2Icon, WorkflowIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import type { Agent } from '@/lib/types'
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { arraysEqual, setsEqual, toggleSetItem } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { ReactFlowProvider } from '@xyflow/react'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { useTriggers, useDeleteTrigger } from '@/lib/hooks/use-triggers'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { VisualSettingsFlow } from '@/components/agent/visual-settings/visual-settings-flow'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { FormMode } from './_components/form-mode/form-mode'
import { RightPanel, type RightTab } from './_components/right-panel/right-panel'

type LeftTab = 'form' | 'visual'

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

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [modelId, setModelId] = useState('')
  const [fallbackIds, setFallbackIds] = useState<string[]>([])
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(new Set())
  const [selectedMcpToolIds, setSelectedMcpToolIds] = useState<Set<string>>(new Set())
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set())
  const [selectedSubAgentIds, setSelectedSubAgentIds] = useState<Set<string>>(new Set())
  const [temperature, setTemperature] = useState(0.7)
  const [topP, setTopP] = useState(1.0)
  const [maxTokens, setMaxTokens] = useState(4096)
  const [selectedMiddlewareTypes, setSelectedMiddlewareTypes] = useState<Set<string>>(new Set())
  const [openerQuestions, setOpenerQuestions] = useState<string[]>([])
  const [leftTab, setLeftTab] = useState<LeftTab>('form')
  const [rightTab, setRightTab] = useState<RightTab>('fix')
  const [initialFixMessage, setInitialFixMessage] = useState<string | undefined>()
  const [justCreated, setJustCreated] = useState(false)
  const [deletingTriggerTarget, setDeletingTriggerTarget] = useState<{
    id: string
    interval: number
  } | null>(null)

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

  // Dirty-aware sync: 사용자가 손대지 않은 필드는 server 값으로 갱신, 손댄 필드는 보존.
  // 채팅 도구(Fix)가 트리거한 invalidate로 폼이 stale 되어 다음 Save가 채팅 변경을
  // silent revert하던 회귀를 막는다. lastSyncedAgentRef = "사용자 baseline server snapshot".
  const lastSyncedAgentRef = useRef<Agent | null>(null)
  // form state는 비교(dirty 판정)용이며, 사용자 입력마다 useEffect가 발화하면
  // dirty 인식이 깨지고 무한 루프 위험. 의존성은 [agent]로만 둔다.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!agent) return
    const prev = lastSyncedAgentRef.current
    const isFirstSync = !prev || prev.id !== agent.id

    if (isFirstSync) {
      // 첫 도착 또는 다른 agent로 navigate → 모두 reset
      setName(agent.name)
      setDescription(agent.description ?? '')
      setSystemPrompt(agent.system_prompt)
      setModelId(agent.model?.id ?? '')
      setFallbackIds(agent.model_fallback_ids ?? [])
      setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
      setSelectedMcpToolIds(new Set(agent.mcp_tools?.map((mt) => mt.id) ?? []))
      setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
      setSelectedSubAgentIds(new Set(agent.sub_agents?.map((sa) => sa.id) ?? []))
      setSelectedMiddlewareTypes(new Set(agent.middleware_configs?.map((mc) => mc.type) ?? []))
      setTemperature(agent.model_params?.temperature ?? 0.7)
      setTopP(agent.model_params?.top_p ?? 1.0)
      setMaxTokens(agent.model_params?.max_tokens ?? 4096)
      setOpenerQuestions(agent.opener_questions ?? [])
    } else if (prev) {
      // 같은 agent의 refetch — 사용자가 손대지 않은 필드만 sync.
      // 비교는 "현재 form state === 직전 server snapshot"이면 dirty 아님 → 새 server 값 반영.
      if (name === prev.name) setName(agent.name)
      if (description === (prev.description ?? '')) setDescription(agent.description ?? '')
      if (systemPrompt === prev.system_prompt) setSystemPrompt(agent.system_prompt)
      if (modelId === (prev.model?.id ?? '')) setModelId(agent.model?.id ?? '')
      if (arraysEqual(fallbackIds, prev.model_fallback_ids ?? [])) {
        setFallbackIds(agent.model_fallback_ids ?? [])
      }

      const prevToolIds = new Set(prev.tools.map((tl) => tl.id))
      if (setsEqual(selectedToolIds, prevToolIds)) {
        setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
      }
      const prevMcpToolIds = new Set(prev.mcp_tools?.map((mt) => mt.id) ?? [])
      if (setsEqual(selectedMcpToolIds, prevMcpToolIds)) {
        setSelectedMcpToolIds(new Set(agent.mcp_tools?.map((mt) => mt.id) ?? []))
      }
      const prevSkillIds = new Set(prev.skills?.map((s) => s.id) ?? [])
      if (setsEqual(selectedSkillIds, prevSkillIds)) {
        setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
      }
      const prevSubAgentIds = new Set(prev.sub_agents?.map((sa) => sa.id) ?? [])
      if (setsEqual(selectedSubAgentIds, prevSubAgentIds)) {
        setSelectedSubAgentIds(new Set(agent.sub_agents?.map((sa) => sa.id) ?? []))
      }
      const prevMwTypes = new Set(prev.middleware_configs?.map((mc) => mc.type) ?? [])
      if (setsEqual(selectedMiddlewareTypes, prevMwTypes)) {
        setSelectedMiddlewareTypes(
          new Set(agent.middleware_configs?.map((mc) => mc.type) ?? []),
        )
      }
      if (arraysEqual(openerQuestions, prev.opener_questions ?? [])) {
        setOpenerQuestions(agent.opener_questions ?? [])
      }
      if (temperature === (prev.model_params?.temperature ?? 0.7)) {
        setTemperature(agent.model_params?.temperature ?? 0.7)
      }
      if (topP === (prev.model_params?.top_p ?? 1.0)) {
        setTopP(agent.model_params?.top_p ?? 1.0)
      }
      if (maxTokens === (prev.model_params?.max_tokens ?? 4096)) {
        setMaxTokens(agent.model_params?.max_tokens ?? 4096)
      }
    }
    lastSyncedAgentRef.current = agent
  }, [agent])
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  const initialToolIds = useMemo(
    () => new Set(agent?.tools.map((tl) => tl.id) ?? []),
    [agent?.tools],
  )
  const initialSkillIds = useMemo(
    () => new Set(agent?.skills?.map((s) => s.id) ?? []),
    [agent?.skills],
  )
  const initialSubAgentIds = useMemo(
    () => new Set(agent?.sub_agents?.map((sa) => sa.id) ?? []),
    [agent?.sub_agents],
  )
  const initialMwTypes = useMemo(
    () => new Set(agent?.middleware_configs?.map((mc) => mc.type) ?? []),
    [agent?.middleware_configs],
  )
  const initialOpenerQuestions = useMemo(
    () => agent?.opener_questions ?? [],
    [agent?.opener_questions],
  )
  const initialFallbackIds = useMemo(
    () => agent?.model_fallback_ids ?? [],
    [agent?.model_fallback_ids],
  )

  const isDirty = useMemo(() => {
    if (!agent) return false
    return (
      name !== agent.name ||
      description !== (agent.description ?? '') ||
      systemPrompt !== agent.system_prompt ||
      modelId !== (agent.model?.id ?? '') ||
      temperature !== (agent.model_params?.temperature ?? 0.7) ||
      topP !== (agent.model_params?.top_p ?? 1.0) ||
      maxTokens !== (agent.model_params?.max_tokens ?? 4096) ||
      !setsEqual(selectedToolIds, initialToolIds) ||
      !setsEqual(selectedSkillIds, initialSkillIds) ||
      !setsEqual(selectedSubAgentIds, initialSubAgentIds) ||
      !setsEqual(selectedMiddlewareTypes, initialMwTypes) ||
      !arraysEqual(openerQuestions, initialOpenerQuestions) ||
      !arraysEqual(fallbackIds, initialFallbackIds)
    )
  }, [
    agent,
    name,
    description,
    systemPrompt,
    modelId,
    temperature,
    topP,
    maxTokens,
    selectedToolIds,
    selectedSkillIds,
    selectedSubAgentIds,
    selectedMiddlewareTypes,
    openerQuestions,
    fallbackIds,
    initialToolIds,
    initialSkillIds,
    initialSubAgentIds,
    initialMwTypes,
    initialOpenerQuestions,
    initialFallbackIds,
  ])

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
      await updateAgent.mutateAsync({
        name,
        description: description || undefined,
        system_prompt: systemPrompt,
        model_id: modelId,
        tool_ids: Array.from(selectedToolIds),
        mcp_tool_ids: Array.from(selectedMcpToolIds),
        skill_ids: Array.from(selectedSkillIds),
        sub_agent_ids: Array.from(selectedSubAgentIds),
        middleware_configs: Array.from(selectedMiddlewareTypes).map((type) => ({
          type,
          params: {},
        })),
        model_params: { temperature, top_p: topP, max_tokens: maxTokens },
        opener_questions: openerQuestions,
        model_fallback_ids: fallbackIds.length > 0 ? fallbackIds : null,
      })
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
    <div className="flex flex-1 flex-col overflow-hidden">
      <header className="flex items-start gap-3 border-b px-6 py-3">
        <Button variant="ghost" size="icon-sm" onClick={handleBack} aria-label={t('back')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <AgentAvatar imageUrl={agent?.image_url ?? null} name={name} size="sm" />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-8 rounded-md border-0 bg-transparent px-2 text-lg font-semibold shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
            placeholder={t('namePlaceholder')}
          />
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="h-7 rounded-md border-0 bg-transparent px-2 text-xs text-muted-foreground shadow-none transition-colors hover:bg-muted/30 focus-visible:bg-muted/40 focus-visible:ring-0"
            placeholder={t('descriptionPlaceholder')}
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <Button variant="ghost" size="icon-sm" aria-label={t('deleteAgent')}>
                  <Trash2Icon className="size-4 text-destructive" />
                </Button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t('deleteDialog.title')}</AlertDialogTitle>
                <AlertDialogDescription>{t('deleteDialog.description')}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDelete}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/80"
                >
                  {deleteAgent.isPending ? (
                    <Loader2Icon className="mr-1 size-4 animate-spin" />
                  ) : null}
                  {tc('delete')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button onClick={handleSave} disabled={updateAgent.isPending || !isDirty}>
            {updateAgent.isPending ? (
              <Loader2Icon className="mr-1 size-4 animate-spin" />
            ) : null}
            {t('save')}
          </Button>
        </div>
      </header>

      <main className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-2">
        <section className="flex min-h-0 flex-col overflow-hidden border-b lg:border-b-0 lg:border-r">
          <Tabs
            value={leftTab}
            onValueChange={(v) => setLeftTab(v as LeftTab)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <div className="sticky top-0 z-10 flex justify-center overflow-hidden bg-background">
              <TabsList variant="line" className="h-auto">
                <TabsTrigger
                  value="form"
                  className="gap-1 px-4 py-2.5 after:bg-emerald-500 data-active:text-emerald-600 dark:after:bg-emerald-400 dark:data-active:text-emerald-400"
                >
                  <ClipboardListIcon className="size-3.5" />
                  {t('tabs.form')}
                </TabsTrigger>
                <TabsTrigger
                  value="visual"
                  className="gap-1 px-4 py-2.5 after:bg-emerald-500 data-active:text-emerald-600 dark:after:bg-emerald-400 dark:data-active:text-emerald-400"
                >
                  <WorkflowIcon className="size-3.5" />
                  {t('tabs.visual')}
                </TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="form" className="flex flex-1 min-h-0 flex-col overflow-hidden">
              <FormMode
                systemPrompt={systemPrompt}
                onSystemPromptChange={setSystemPrompt}
                selectedSubAgentIds={selectedSubAgentIds}
                onToggleSubAgent={(id) =>
                  setSelectedSubAgentIds((prev) => toggleSetItem(prev, id))
                }
                currentAgentId={agentId}
                modelId={modelId}
                onModelIdChange={setModelId}
                temperature={temperature}
                onTemperatureChange={setTemperature}
                topP={topP}
                onTopPChange={setTopP}
                maxTokens={maxTokens}
                onMaxTokensChange={setMaxTokens}
                onResetModelParams={() => {
                  setTemperature(0.7)
                  setTopP(1.0)
                  setMaxTokens(4096)
                }}
                fallbackIds={fallbackIds}
                onFallbackIdsChange={setFallbackIds}
                selectedToolIds={selectedToolIds}
                onToggleTool={(id) =>
                  setSelectedToolIds((prev) => toggleSetItem(prev, id))
                }
                selectedMcpToolIds={selectedMcpToolIds}
                onToggleMcpTool={(id) =>
                  setSelectedMcpToolIds((prev) => toggleSetItem(prev, id))
                }
                selectedSkillIds={selectedSkillIds}
                onToggleSkill={(id) =>
                  setSelectedSkillIds((prev) => toggleSetItem(prev, id))
                }
                selectedMiddlewareTypes={selectedMiddlewareTypes}
                onToggleMiddleware={(type) =>
                  setSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type))
                }
              />
            </TabsContent>
            <TabsContent value="visual" className="flex flex-1 min-h-0 flex-col overflow-hidden p-0">
              {agent && (
                <ReactFlowProvider>
                  <VisualSettingsFlow
                    agent={agent}
                    agentId={agentId}
                    models={models ?? []}
                    tools={tools ?? []}
                    skills={skills ?? []}
                    middlewares={middlewares ?? []}
                    triggers={triggers ?? []}
                    embedded
                    controlledState={{
                      name,
                      description,
                      systemPrompt,
                      modelId,
                      temperature,
                      topP,
                      maxTokens,
                      selectedToolIds,
                      selectedMcpToolIds,
                      selectedSkillIds,
                      selectedSubAgentIds,
                      selectedMiddlewareTypes,
                    }}
                    controlledHandlers={{
                      onNameChange: setName,
                      onDescriptionChange: setDescription,
                      onSystemPromptChange: setSystemPrompt,
                      onModelIdChange: setModelId,
                      onTemperatureChange: setTemperature,
                      onTopPChange: setTopP,
                      onMaxTokensChange: setMaxTokens,
                      onToggleTool: (id) =>
                        setSelectedToolIds((prev) => toggleSetItem(prev, id)),
                      onToggleMcpTool: (id) =>
                        setSelectedMcpToolIds((prev) => toggleSetItem(prev, id)),
                      onToggleSkill: (id) =>
                        setSelectedSkillIds((prev) => toggleSetItem(prev, id)),
                      onToggleSubAgent: (id) =>
                        setSelectedSubAgentIds((prev) => toggleSetItem(prev, id)),
                      onToggleMiddleware: (type) =>
                        setSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type)),
                    }}
                  />
                </ReactFlowProvider>
              )}
            </TabsContent>
          </Tabs>
        </section>

        <section className="flex min-h-0 flex-col overflow-hidden">
          <RightPanel
            tab={rightTab}
            onTabChange={setRightTab}
            agentId={agentId}
            agentName={agent?.name ?? ''}
            agentImageUrl={agent?.image_url ?? null}
            openerQuestions={openerQuestions}
            onOpenerQuestionsChange={setOpenerQuestions}
            onRequestDeleteTrigger={setDeletingTriggerTarget}
            initialFixMessage={initialFixMessage}
            createMode={justCreated}
          />
        </section>
      </main>

      {/* Trigger Delete Confirm */}
      <DeleteConfirmDialog
        open={!!deletingTriggerTarget}
        onOpenChange={(v) => !v && setDeletingTriggerTarget(null)}
        title={t('trigger.deleteConfirm')}
        description={
          deletingTriggerTarget
            ? t('trigger.interval', { minutes: deletingTriggerTarget.interval })
            : ''
        }
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
