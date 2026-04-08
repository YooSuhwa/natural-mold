'use client'

import { use, useState, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeftIcon, Loader2Icon, Trash2Icon, SaveIcon, SparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { useDeleteTrigger } from '@/lib/hooks/use-triggers'
import { toggleSetItem, setsEqual } from '@/lib/utils'
import { Button } from '@/components/ui/button'
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
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/shared/page-header'
import { AssistantPanel } from '@/components/agent/assistant-panel'
import { BasicInfoTab } from './_components/basic-info-tab'
import { ModelTab } from './_components/model-tab'
import { ToolsSkillsTab } from './_components/tools-skills-tab'
import { TriggersTab } from './_components/triggers-tab'

export default function AgentSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const router = useRouter()
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const updateAgent = useUpdateAgent(agentId)
  const deleteAgent = useDeleteAgent()
  const deleteTrigger = useDeleteTrigger(agentId)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [modelId, setModelId] = useState('')
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(new Set())
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set())
  const [temperature, setTemperature] = useState(0.7)
  const [topP, setTopP] = useState(1.0)
  const [maxTokens, setMaxTokens] = useState(4096)
  const [selectedMiddlewareTypes, setSelectedMiddlewareTypes] = useState<Set<string>>(new Set())
  const [deletingTriggerTarget, setDeletingTriggerTarget] = useState<{
    id: string
    interval: number
  } | null>(null)

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setDescription(agent.description ?? '')
      setSystemPrompt(agent.system_prompt)
      setModelId(agent.model.id)
      setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
      setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
      setSelectedMiddlewareTypes(new Set(agent.middleware_configs?.map((mc) => mc.type) ?? []))
      setTemperature(agent.model_params?.temperature ?? 0.7)
      setTopP(agent.model_params?.top_p ?? 1.0)
      setMaxTokens(agent.model_params?.max_tokens ?? 4096)
    }
  }, [agent])
  /* eslint-enable react-hooks/set-state-in-effect */

  const initialToolIds = useMemo(() => new Set(agent?.tools.map((tl) => tl.id) ?? []), [agent])
  const initialSkillIds = useMemo(() => new Set(agent?.skills?.map((s) => s.id) ?? []), [agent])
  const initialMwTypes = useMemo(
    () => new Set(agent?.middleware_configs?.map((mc) => mc.type) ?? []),
    [agent],
  )

  const isDirty = useMemo(() => {
    if (!agent) return false
    return (
      name !== agent.name ||
      description !== (agent.description ?? '') ||
      systemPrompt !== agent.system_prompt ||
      modelId !== agent.model.id ||
      temperature !== (agent.model_params?.temperature ?? 0.7) ||
      topP !== (agent.model_params?.top_p ?? 1.0) ||
      maxTokens !== (agent.model_params?.max_tokens ?? 4096) ||
      !setsEqual(selectedToolIds, initialToolIds) ||
      !setsEqual(selectedSkillIds, initialSkillIds) ||
      !setsEqual(selectedMiddlewareTypes, initialMwTypes)
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
    selectedMiddlewareTypes,
    initialToolIds,
    initialSkillIds,
    initialMwTypes,
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
        skill_ids: Array.from(selectedSkillIds),
        middleware_configs: Array.from(selectedMiddlewareTypes).map((type) => ({
          type,
          params: {},
        })),
        model_params: { temperature, top_p: topP, max_tokens: maxTokens },
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
    <div className="flex flex-1 flex-col overflow-auto">
      <div className="flex-1 space-y-6 p-6">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              if (isDirty && !window.confirm(t('unsavedWarning'))) return
              const hasAppHistory =
                document.referrer && new URL(document.referrer).origin === window.location.origin
              if (hasAppHistory) {
                router.back()
              } else {
                router.push(`/agents/${agentId}`)
              }
            }}
          >
            <ArrowLeftIcon className="size-4" />
          </Button>
          <span className="text-sm text-muted-foreground">{t('back')}</span>
        </div>

        <div className="flex items-center justify-between">
          <PageHeader title={t('title', { name: agent?.name ?? '' })} />
        </div>

        <div className="mx-auto w-full max-w-2xl">
          <Tabs defaultValue="basic">
            <TabsList className="sticky top-0 z-10 overflow-x-auto scrollbar-none border-b bg-background/95 backdrop-blur-sm">
              <TabsTrigger value="basic">{t('tabs.basic')}</TabsTrigger>
              <TabsTrigger value="model">{t('tabs.model')}</TabsTrigger>
              <TabsTrigger value="tools">{t('tabs.tools')}</TabsTrigger>
              <TabsTrigger value="triggers">{t('tabs.triggers')}</TabsTrigger>
              <TabsTrigger value="assistant" className="gap-1">
                <SparklesIcon className="size-3.5" />
                {t('tabs.assistant')}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="pt-6">
              <BasicInfoTab
                name={name}
                onNameChange={setName}
                description={description}
                onDescriptionChange={setDescription}
                systemPrompt={systemPrompt}
                onSystemPromptChange={setSystemPrompt}
              />
            </TabsContent>

            <TabsContent value="model" className="pt-6">
              <ModelTab
                modelId={modelId}
                onModelIdChange={setModelId}
                temperature={temperature}
                onTemperatureChange={setTemperature}
                topP={topP}
                onTopPChange={setTopP}
                maxTokens={maxTokens}
                onMaxTokensChange={setMaxTokens}
                onReset={() => {
                  setTemperature(0.7)
                  setTopP(1.0)
                  setMaxTokens(4096)
                }}
              />
            </TabsContent>

            <TabsContent value="tools" className="pt-6">
              <ToolsSkillsTab
                selectedToolIds={selectedToolIds}
                onToggleTool={(id) => setSelectedToolIds((prev) => toggleSetItem(prev, id))}
                selectedSkillIds={selectedSkillIds}
                onToggleSkill={(id) => setSelectedSkillIds((prev) => toggleSetItem(prev, id))}
                selectedMiddlewareTypes={selectedMiddlewareTypes}
                onToggleMiddleware={(type) =>
                  setSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type))
                }
              />
            </TabsContent>

            <TabsContent value="triggers" className="pt-6">
              <TriggersTab agentId={agentId} onRequestDelete={setDeletingTriggerTarget} />
            </TabsContent>

            <TabsContent value="assistant" className="pt-6">
              {agent && <AssistantPanel agentId={agentId} agentName={agent.name} />}
            </TabsContent>
          </Tabs>
        </div>
      </div>

      {/* Sticky Save Bar */}
      <div className="sticky bottom-0 flex flex-col-reverse gap-2 border-t bg-background/95 px-6 py-3 backdrop-blur-sm sm:flex-row sm:items-center sm:justify-between">
        <Button onClick={handleSave} disabled={updateAgent.isPending || !isDirty}>
          {updateAgent.isPending ? (
            <Loader2Icon className="mr-1 size-4 animate-spin" />
          ) : (
            <SaveIcon className="size-4" data-icon="inline-start" />
          )}
          {t('save')}
        </Button>

        <AlertDialog>
          <AlertDialogTrigger
            render={
              <Button variant="destructive">
                <Trash2Icon className="size-4" data-icon="inline-start" />
                {t('deleteAgent')}
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
      </div>

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
