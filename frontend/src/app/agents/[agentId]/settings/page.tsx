'use client'

import { use, useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeftIcon,
  Loader2Icon,
  Trash2Icon,
  SaveIcon,
  TimerIcon,
  PlayIcon,
  PauseIcon,
  PlusIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations, useFormatter } from 'next-intl'
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
import { useSkills } from '@/lib/hooks/use-skills'
import {
  useTriggers,
  useCreateTrigger,
  useUpdateTrigger,
  useDeleteTrigger,
} from '@/lib/hooks/use-triggers'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/shared/page-header'
import { FixAgentDialog } from '@/components/agent/fix-agent-dialog'

export default function AgentSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const router = useRouter()
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')
  const format = useFormatter()
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
  const { data: skills } = useSkills()
  const updateAgent = useUpdateAgent(agentId)
  const deleteAgent = useDeleteAgent()
  const { data: triggers } = useTriggers(agentId)
  const createTrigger = useCreateTrigger(agentId)
  const updateTrigger = useUpdateTrigger(agentId)
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
  const [showTriggerForm, setShowTriggerForm] = useState(false)
  const [triggerMinutes, setTriggerMinutes] = useState('10')
  const [triggerMessage, setTriggerMessage] = useState('')

  const noToolsParts = String(t.raw('noTools')).split('{link}')
  const noSkillsParts = String(t.raw('noSkills')).split('{link}')

  // Sync form state when agent data loads from server.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setDescription(agent.description ?? '')
      setSystemPrompt(agent.system_prompt)
      setModelId(agent.model.id)
      setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
      setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
      setTemperature(agent.model_params?.temperature ?? 0.7)
      setTopP(agent.model_params?.top_p ?? 1.0)
      setMaxTokens(agent.model_params?.max_tokens ?? 4096)
    }
  }, [agent])
  /* eslint-enable react-hooks/set-state-in-effect */

  async function handleSave() {
    try {
      await updateAgent.mutateAsync({
        name,
        description: description || undefined,
        system_prompt: systemPrompt,
        model_id: modelId,
        tool_ids: Array.from(selectedToolIds),
        skill_ids: Array.from(selectedSkillIds),
        model_params: { temperature, top_p: topP, max_tokens: maxTokens },
      })
      toast.success(t('toast.saved'))
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  async function handleDelete() {
    await deleteAgent.mutateAsync(agentId)
    router.push('/')
  }

  function toggleTool(toolId: string) {
    setSelectedToolIds((prev) => {
      const next = new Set(prev)
      if (next.has(toolId)) {
        next.delete(toolId)
      } else {
        next.add(toolId)
      }
      return next
    })
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
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <div className="flex items-center gap-2">
        <Link href={`/agents/${agentId}`}>
          <Button variant="ghost" size="icon-sm">
            <ArrowLeftIcon className="size-4" />
          </Button>
        </Link>
        <span className="text-sm text-muted-foreground">{t('backToChat')}</span>
      </div>

      <div className="flex items-center justify-between">
        <PageHeader title={t('title', { name: agent?.name ?? '' })} />
        {agent && <FixAgentDialog agentId={agentId} agentName={agent.name} />}
      </div>

      <div className="mx-auto w-full max-w-2xl space-y-6">
        {/* Name */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('name')}</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>

        {/* Description */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('description')}</label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('descriptionPlaceholder')}
          />
        </div>

        {/* System prompt */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('systemPrompt')}</label>
          <Textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={8}
            className="font-mono text-xs"
          />
        </div>

        {/* Model */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('model')}</label>
          {models ? (
            <Select
              value={modelId}
              onValueChange={(val) => {
                if (val) setModelId(val)
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t('modelPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.id} value={model.id}>
                    {model.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Skeleton className="h-8 w-full" />
          )}
        </div>

        {/* Model Parameters */}
        <div className="space-y-4 rounded-lg border p-4">
          <label className="text-sm font-medium">{t('modelParams')}</label>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Temperature</span>
              <span className="font-mono text-xs tabular-nums">{temperature.toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
              className="w-full accent-primary"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>{t('temperature.accurate')}</span>
              <span>{t('temperature.creative')}</span>
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Top P</span>
              <span className="font-mono text-xs tabular-nums">{topP.toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={topP}
              onChange={(e) => setTopP(Number(e.target.value))}
              className="w-full accent-primary"
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Max Tokens</span>
            </div>
            <Input
              type="number"
              min="256"
              max="32768"
              step="256"
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value) || 4096)}
            />
          </div>

          <Button
            variant="ghost"
            size="sm"
            className="text-xs text-muted-foreground"
            onClick={() => {
              setTemperature(0.7)
              setTopP(1.0)
              setMaxTokens(4096)
            }}
          >
            {t('resetToDefault')}
          </Button>
        </div>

        {/* Tools */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('tools')}</label>
          {tools ? (
            tools.length > 0 ? (
              <div className="space-y-2 rounded-lg border p-3">
                {tools.map((tool) => (
                  <label key={tool.id} className="flex items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      checked={selectedToolIds.has(tool.id)}
                      onChange={() => toggleTool(tool.id)}
                      className="size-4 rounded border-input"
                    />
                    <span>{tool.name}</span>
                    {tool.description && (
                      <span className="text-xs text-muted-foreground">- {tool.description}</span>
                    )}
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {noToolsParts[0]}
                <Link href="/tools" className="text-primary hover:underline">
                  {t('toolsLink')}
                </Link>
                {noToolsParts[1]}
              </p>
            )
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </div>

        {/* Skills */}
        <div className="space-y-2">
          <label className="text-sm font-medium">{t('skills')}</label>
          {skills ? (
            skills.length > 0 ? (
              <div className="space-y-2 rounded-lg border p-3">
                {skills.map((skill) => (
                  <label key={skill.id} className="flex items-center gap-3 text-sm">
                    <input
                      type="checkbox"
                      checked={selectedSkillIds.has(skill.id)}
                      onChange={() => {
                        setSelectedSkillIds((prev) => {
                          const next = new Set(prev)
                          if (next.has(skill.id)) next.delete(skill.id)
                          else next.add(skill.id)
                          return next
                        })
                      }}
                      className="size-4 rounded border-input"
                    />
                    <span>{skill.name}</span>
                    {skill.description && (
                      <span className="text-xs text-muted-foreground">- {skill.description}</span>
                    )}
                  </label>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                {noSkillsParts[0]}
                <Link href="/skills" className="text-primary hover:underline">
                  {t('skillsLink')}
                </Link>
                {noSkillsParts[1]}
              </p>
            )
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </div>

        {/* Triggers */}
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <TimerIcon className="size-4" />
            {t('trigger.title')}
          </label>

          {triggers && triggers.length > 0 ? (
            <div className="space-y-2">
              {triggers.map((trigger) => (
                <Card key={trigger.id}>
                  <CardContent className="flex items-center justify-between py-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge variant={trigger.status === 'active' ? 'default' : 'secondary'}>
                          {trigger.status === 'active' ? t('trigger.active') : t('trigger.paused')}
                        </Badge>
                        <span className="text-sm">
                          {t('trigger.interval', {
                            minutes: trigger.schedule_config.interval_minutes ?? 10,
                          })}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate max-w-md">
                        &quot;{trigger.input_message}&quot;
                      </p>
                      <div className="flex gap-3 text-xs text-muted-foreground">
                        {trigger.last_run_at && (
                          <span>
                            {t('trigger.lastRun', {
                              date: format.dateTime(new Date(trigger.last_run_at), {
                                dateStyle: 'medium',
                                timeStyle: 'short',
                              }),
                            })}
                          </span>
                        )}
                        <span>{t('trigger.runCount', { count: trigger.run_count })}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={
                          trigger.status === 'active' ? t('trigger.pause') : t('trigger.resume')
                        }
                        onClick={() =>
                          updateTrigger.mutate({
                            triggerId: trigger.id,
                            data: {
                              status: trigger.status === 'active' ? 'paused' : 'active',
                            },
                          })
                        }
                      >
                        {trigger.status === 'active' ? (
                          <PauseIcon className="size-4" />
                        ) : (
                          <PlayIcon className="size-4" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t('trigger.delete')}
                        onClick={() => deleteTrigger.mutate(trigger.id)}
                      >
                        <Trash2Icon className="size-4 text-muted-foreground" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : null}

          {showTriggerForm ? (
            <div className="space-y-3 rounded-lg border p-4">
              <div className="space-y-2">
                <label className="text-xs font-medium">{t('trigger.intervalLabel')}</label>
                <Input
                  type="number"
                  min="1"
                  value={triggerMinutes}
                  onChange={(e) => setTriggerMinutes(e.target.value)}
                  placeholder="10"
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-medium">{t('trigger.messageLabel')}</label>
                <Textarea
                  value={triggerMessage}
                  onChange={(e) => setTriggerMessage(e.target.value)}
                  placeholder={t('trigger.messagePlaceholder')}
                  rows={2}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={createTrigger.isPending || !triggerMessage.trim()}
                  onClick={async () => {
                    await createTrigger.mutateAsync({
                      trigger_type: 'interval',
                      schedule_config: { interval_minutes: Number(triggerMinutes) || 10 },
                      input_message: triggerMessage.trim(),
                    })
                    setShowTriggerForm(false)
                    setTriggerMessage('')
                    setTriggerMinutes('10')
                  }}
                >
                  {createTrigger.isPending && <Loader2Icon className="mr-1 size-3 animate-spin" />}
                  {t('trigger.addButton')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setShowTriggerForm(false)}>
                  {tc('cancel')}
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setShowTriggerForm(true)}>
              <PlusIcon className="size-4" data-icon="inline-start" />
              {t('trigger.addNew')}
            </Button>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between pt-4">
          <Button onClick={handleSave} disabled={updateAgent.isPending}>
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
      </div>
    </div>
  )
}
