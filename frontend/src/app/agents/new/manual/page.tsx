'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowLeftIcon,
  ClipboardListIcon,
  Loader2Icon,
  WorkflowIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { ReactFlowProvider } from '@xyflow/react'
import { useCreateAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import { useMiddlewares } from '@/lib/hooks/use-middlewares'
import { toggleSetItem } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { VisualSettingsFlow } from '@/components/agent/visual-settings/visual-settings-flow'
import { FormMode } from '@/app/agents/[agentId]/settings/_components/form-mode/form-mode'
import {
  RightPanel,
  type RightTab,
} from '@/app/agents/[agentId]/settings/_components/right-panel/right-panel'

type LeftTab = 'form' | 'visual'

export default function ManualCreationPage() {
  const router = useRouter()
  const t = useTranslations('agent.settings')
  const { data: models, isLoading: modelsLoading } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const { data: middlewares } = useMiddlewares()
  const createAgent = useCreateAgent()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [modelId, setModelId] = useState('')
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(new Set())
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(new Set())
  const [selectedSubAgentIds, setSelectedSubAgentIds] = useState<Set<string>>(new Set())
  const [temperature, setTemperature] = useState(0.7)
  const [topP, setTopP] = useState(1.0)
  const [maxTokens, setMaxTokens] = useState(4096)
  const [selectedMiddlewareTypes, setSelectedMiddlewareTypes] = useState<Set<string>>(new Set())
  const [openerQuestions, setOpenerQuestions] = useState<string[]>([])
  const [leftTab, setLeftTab] = useState<LeftTab>('form')
  const [rightTab, setRightTab] = useState<RightTab>('fix')

  // 첫 모델을 기본값으로 prefill
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!modelId && models && models.length > 0) {
      setModelId(models[0].id)
    }
  }, [models, modelId])
  /* eslint-enable react-hooks/set-state-in-effect */

  const canSave = name.trim().length > 0 && modelId.length > 0

  function buildCreateRequest() {
    return {
      name: name.trim() || t('defaultName'),
      description: description.trim() || undefined,
      system_prompt: systemPrompt,
      model_id: modelId,
      tool_ids: Array.from(selectedToolIds),
      skill_ids: Array.from(selectedSkillIds),
      sub_agent_ids: Array.from(selectedSubAgentIds),
      middleware_configs: Array.from(selectedMiddlewareTypes).map((type) => ({
        type,
        params: {},
      })),
      model_params: { temperature, top_p: topP, max_tokens: maxTokens },
      opener_questions: openerQuestions,
    }
  }

  async function handleSave() {
    try {
      const created = await createAgent.mutateAsync(buildCreateRequest())
      toast.success(t('toast.saved'))
      router.replace(`/agents/${created.id}/settings`)
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  async function handleCreateModeFirstMessage(msg: string) {
    try {
      const created = await createAgent.mutateAsync(buildCreateRequest())
      // sessionStorage로 첫 메시지 carry → settings 페이지에서 자동 전송
      sessionStorage.setItem('fix-initial-message', msg)
      router.replace(`/agents/${created.id}/settings`)
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  if (modelsLoading || !models?.length) {
    return (
      <div className="flex flex-1 flex-col gap-4 p-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-[calc(100vh-10rem)] w-full" />
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <header className="flex items-start gap-3 border-b px-6 py-3">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => router.back()}
          aria-label={t('back')}
        >
          <ArrowLeftIcon className="size-4" />
        </Button>
        <AgentAvatar imageUrl={null} name={name} size="sm" />
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
          <Button onClick={handleSave} disabled={!canSave || createAgent.isPending}>
            {createAgent.isPending ? (
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
              currentAgentId=""
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
          <TabsContent value="visual" className="flex flex-1 min-h-0 flex-col overflow-hidden p-0">
            <ReactFlowProvider>
              <VisualSettingsFlow
                models={models}
                tools={tools ?? []}
                skills={skills ?? []}
                middlewares={middlewares ?? []}
                mode="create"
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
                  onToggleSkill: (id) =>
                    setSelectedSkillIds((prev) => toggleSetItem(prev, id)),
                  onToggleSubAgent: (id) =>
                    setSelectedSubAgentIds((prev) => toggleSetItem(prev, id)),
                  onToggleMiddleware: (type) =>
                    setSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type)),
                }}
              />
            </ReactFlowProvider>
          </TabsContent>
        </Tabs>
        </section>

        <section className="flex min-h-0 flex-col overflow-hidden">
          <RightPanel
            tab={rightTab}
            onTabChange={setRightTab}
            agentId=""
            agentName={name}
            agentImageUrl={null}
            openerQuestions={openerQuestions}
            onOpenerQuestionsChange={setOpenerQuestions}
            onRequestDeleteTrigger={() => {}}
            createMode
            onCreateModeFirstMessage={handleCreateModeFirstMessage}
          />
        </section>
      </main>
    </div>
  )
}
