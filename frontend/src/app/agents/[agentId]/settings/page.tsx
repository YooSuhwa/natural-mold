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
import { useAgent, useUpdateAgent, useDeleteAgent } from '@/lib/hooks/use-agents'
import { useModels } from '@/lib/hooks/use-models'
import { useTools } from '@/lib/hooks/use-tools'
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

export default function AgentSettingsPage({ params }: { params: Promise<{ agentId: string }> }) {
  const { agentId } = use(params)
  const router = useRouter()
  const { data: agent, isLoading: agentLoading } = useAgent(agentId)
  const { data: models } = useModels()
  const { data: tools } = useTools()
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
  const [showTriggerForm, setShowTriggerForm] = useState(false)
  const [triggerMinutes, setTriggerMinutes] = useState('10')
  const [triggerMessage, setTriggerMessage] = useState('')

  // Sync form state when agent data loads from server.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (agent) {
      setName(agent.name)
      setDescription(agent.description ?? '')
      setSystemPrompt(agent.system_prompt)
      setModelId(agent.model.id)
      setSelectedToolIds(new Set(agent.tools.map((t) => t.id)))
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
      })
      toast.success('저장되었습니다')
    } catch {
      toast.error('저장에 실패했습니다')
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
        <span className="text-sm text-muted-foreground">채팅으로 돌아가기</span>
      </div>

      <PageHeader title={`에이전트 설정: ${agent?.name ?? ''}`} />

      <div className="mx-auto w-full max-w-2xl space-y-6">
        {/* Name */}
        <div className="space-y-2">
          <label className="text-sm font-medium">이름</label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>

        {/* Description */}
        <div className="space-y-2">
          <label className="text-sm font-medium">설명</label>
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="에이전트에 대한 간단한 설명"
          />
        </div>

        {/* System prompt */}
        <div className="space-y-2">
          <label className="text-sm font-medium">시스템 프롬프트</label>
          <Textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={8}
            className="font-mono text-xs"
          />
        </div>

        {/* Model */}
        <div className="space-y-2">
          <label className="text-sm font-medium">모델</label>
          {models ? (
            <Select
              value={modelId}
              onValueChange={(val) => {
                if (val) setModelId(val)
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="모델 선택" />
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

        {/* Tools */}
        <div className="space-y-2">
          <label className="text-sm font-medium">연결된 도구</label>
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
                등록된 도구가 없습니다.{' '}
                <Link href="/tools" className="text-primary hover:underline">
                  도구 관리
                </Link>
                에서 추가해주세요.
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
            자동 실행 (트리거)
          </label>

          {triggers && triggers.length > 0 ? (
            <div className="space-y-2">
              {triggers.map((trigger) => (
                <Card key={trigger.id}>
                  <CardContent className="flex items-center justify-between py-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge variant={trigger.status === 'active' ? 'default' : 'secondary'}>
                          {trigger.status === 'active' ? '활성' : '일시정지'}
                        </Badge>
                        <span className="text-sm">
                          매 {trigger.schedule_config.interval_minutes}분
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground truncate max-w-md">
                        &quot;{trigger.input_message}&quot;
                      </p>
                      <div className="flex gap-3 text-xs text-muted-foreground">
                        {trigger.last_run_at && (
                          <span>
                            마지막 실행: {new Date(trigger.last_run_at).toLocaleString('ko-KR')}
                          </span>
                        )}
                        <span>실행 횟수: {trigger.run_count}회</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={trigger.status === 'active' ? '일시정지' : '재개'}
                        onClick={() =>
                          updateTrigger.mutate({
                            triggerId: trigger.id,
                            data: { status: trigger.status === 'active' ? 'paused' : 'active' },
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
                        aria-label="트리거 삭제"
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
                <label className="text-xs font-medium">실행 간격 (분)</label>
                <Input
                  type="number"
                  min="1"
                  value={triggerMinutes}
                  onChange={(e) => setTriggerMinutes(e.target.value)}
                  placeholder="10"
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-medium">실행 시 보낼 메시지</label>
                <Textarea
                  value={triggerMessage}
                  onChange={(e) => setTriggerMessage(e.target.value)}
                  placeholder="예: 한글과컴퓨터 최신 뉴스를 검색하고 요약해줘"
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
                  트리거 추가
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setShowTriggerForm(false)}>
                  취소
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setShowTriggerForm(true)}>
              <PlusIcon className="size-4" data-icon="inline-start" />
              자동 실행 추가
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
            저장
          </Button>

          <AlertDialog>
            <AlertDialogTrigger
              render={
                <Button variant="destructive">
                  <Trash2Icon className="size-4" data-icon="inline-start" />
                  에이전트 삭제
                </Button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>에이전트를 삭제하시겠습니까?</AlertDialogTitle>
                <AlertDialogDescription>
                  이 작업은 되돌릴 수 없습니다. 에이전트와 관련된 모든 대화가 삭제됩니다.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>취소</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDelete}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/80"
                >
                  {deleteAgent.isPending ? (
                    <Loader2Icon className="mr-1 size-4 animate-spin" />
                  ) : null}
                  삭제
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </div>
  )
}
