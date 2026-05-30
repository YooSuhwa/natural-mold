'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircleIcon,
  CalendarClockIcon,
  ExternalLinkIcon,
  HistoryIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  Trash2Icon,
} from 'lucide-react'
import { useFormatter } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ScheduleDialog } from '@/components/agent/visual-settings/dialogs/schedule-dialog'
import {
  useAllTriggers,
  useDeleteTriggerGlobal,
  useRunTriggerNow,
  useTriggerRuns,
  useUpdateTriggerGlobal,
} from '@/lib/hooks/use-triggers'
import type { AgentTrigger, TriggerCreateRequest, TriggerUpdateRequest } from '@/lib/types'

function formatSchedule(trigger: AgentTrigger) {
  if (trigger.trigger_type === 'interval') {
    return `매 ${trigger.schedule_config.interval_minutes ?? 10}분`
  }
  if (trigger.trigger_type === 'one_time') {
    return trigger.schedule_config.scheduled_at ?? '1회 실행'
  }
  return trigger.schedule_config.cron_expression ?? 'Cron'
}

function statusLabel(status: AgentTrigger['status']) {
  if (status === 'active') return '활성'
  if (status === 'paused') return '일시정지'
  if (status === 'completed') return '완료'
  return '오류'
}

function statusVariant(status: AgentTrigger['status']): 'default' | 'secondary' | 'destructive' {
  if (status === 'active') return 'default'
  if (status === 'error') return 'destructive'
  return 'secondary'
}

export default function SchedulesPage() {
  const { data: triggers, isLoading } = useAllTriggers()
  const updateTrigger = useUpdateTriggerGlobal()
  const deleteTrigger = useDeleteTriggerGlobal()
  const runNow = useRunTriggerNow()
  const format = useFormatter()

  const [editingTrigger, setEditingTrigger] = useState<AgentTrigger | null>(null)
  const [historyTrigger, setHistoryTrigger] = useState<AgentTrigger | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AgentTrigger | null>(null)
  const { data: runs, isLoading: runsLoading } = useTriggerRuns(historyTrigger?.id ?? null)

  const totalUnread = useMemo(
    () =>
      (triggers ?? []).reduce(
        (sum, trigger) => sum + (trigger.schedule_conversation_unread_count ?? 0),
        0,
      ),
    [triggers],
  )
  const activeCount = useMemo(
    () => (triggers ?? []).filter((trigger) => trigger.status === 'active').length,
    [triggers],
  )

  function formatDate(value: string | null) {
    if (!value) return '없음'
    return format.dateTime(new Date(value), {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Seoul',
    })
  }

  function handleToggle(trigger: AgentTrigger) {
    updateTrigger.mutate({
      triggerId: trigger.id,
      data: { status: trigger.status === 'active' ? 'paused' : 'active' },
    })
  }

  function handleEdit(
    payload: TriggerCreateRequest | { triggerId: string; data: TriggerUpdateRequest },
  ) {
    if (!editingTrigger) return
    const data = 'data' in payload ? payload.data : payload
    updateTrigger.mutate(
      { triggerId: editingTrigger.id, data },
      { onSuccess: () => setEditingTrigger(null) },
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <CalendarClockIcon className="size-4" />
            자동 실행
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">스케줄</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            모든 에이전트의 예약 실행과 읽지 않은 자동 응답을 한 곳에서 확인합니다.
          </p>
        </div>
        <div className="flex gap-2">
          <Badge variant="secondary">활성 {activeCount}</Badge>
          {totalUnread > 0 ? <Badge>{totalUnread}개 안읽음</Badge> : null}
        </div>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">전체 스케줄</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : triggers && triggers.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b bg-muted/40 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium">스케줄</th>
                    <th className="px-4 py-3 text-left font-medium">에이전트</th>
                    <th className="px-4 py-3 text-left font-medium">주기</th>
                    <th className="px-4 py-3 text-left font-medium">상태</th>
                    <th className="px-4 py-3 text-left font-medium">다음 실행</th>
                    <th className="px-4 py-3 text-left font-medium">마지막 실행</th>
                    <th className="px-4 py-3 text-left font-medium">결과</th>
                    <th className="px-4 py-3 text-right font-medium">작업</th>
                  </tr>
                </thead>
                <tbody>
                  {triggers.map((trigger) => (
                    <tr key={trigger.id} className="border-b last:border-0">
                      <td className="px-4 py-3 align-top">
                        <div className="font-medium">{trigger.name}</div>
                        <div className="mt-1 max-w-xs truncate text-xs text-muted-foreground">
                          {trigger.input_message}
                        </div>
                        {trigger.last_error ? (
                          <div className="mt-1 flex items-center gap-1 text-xs text-destructive">
                            <AlertCircleIcon className="size-3" />
                            {trigger.last_error}
                          </div>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <Link
                          href={`/agents/${trigger.agent_id}/settings`}
                          className="text-primary-strong hover:underline"
                        >
                          {trigger.agent_name ?? '에이전트'}
                        </Link>
                      </td>
                      <td className="px-4 py-3 align-top font-mono text-xs">
                        {formatSchedule(trigger)}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <Badge variant={statusVariant(trigger.status)}>
                          {statusLabel(trigger.status)}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 align-top text-muted-foreground">
                        {formatDate(trigger.next_run_at)}
                      </td>
                      <td className="px-4 py-3 align-top text-muted-foreground">
                        {formatDate(trigger.last_run_at)}
                      </td>
                      <td className="px-4 py-3 align-top">
                        {trigger.schedule_conversation_id ? (
                          <Link
                            href={`/agents/${trigger.agent_id}/conversations/${trigger.schedule_conversation_id}`}
                            className="inline-flex items-center gap-1 text-primary-strong hover:underline"
                          >
                            {trigger.schedule_conversation_title ?? '결과 대화'}
                            <ExternalLinkIcon className="size-3" />
                            {(trigger.schedule_conversation_unread_count ?? 0) > 0 ? (
                              <Badge className="ml-1" variant="secondary">
                                {trigger.schedule_conversation_unread_count}
                              </Badge>
                            ) : null}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">아직 없음</span>
                        )}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label="즉시 실행"
                            onClick={() => runNow.mutate(trigger.id)}
                            disabled={runNow.isPending}
                          >
                            <RefreshCwIcon className="size-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={trigger.status === 'active' ? '일시정지' : '재개'}
                            onClick={() => handleToggle(trigger)}
                            disabled={updateTrigger.isPending || trigger.status === 'completed'}
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
                            aria-label="수정"
                            onClick={() => setEditingTrigger(trigger)}
                          >
                            <PencilIcon className="size-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label="실행 이력"
                            onClick={() => setHistoryTrigger(trigger)}
                          >
                            <HistoryIcon className="size-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label="삭제"
                            onClick={() => setDeleteTarget(trigger)}
                          >
                            <Trash2Icon className="size-4 text-muted-foreground" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-10 text-center text-sm text-muted-foreground">
              아직 등록된 스케줄이 없습니다. 에이전트 설정에서 자동 실행을 추가해 보세요.
            </div>
          )}
        </CardContent>
      </Card>

      <ScheduleDialog
        open={!!editingTrigger}
        onOpenChange={(open) => !open && setEditingTrigger(null)}
        trigger={editingTrigger}
        isPending={updateTrigger.isPending}
        onSubmit={handleEdit}
      />

      <DialogShell
        open={!!historyTrigger}
        onOpenChange={(open) => !open && setHistoryTrigger(null)}
        size="lg"
        height="auto"
      >
        <DialogShell.Header title="실행 이력" description={historyTrigger?.name} />
        <DialogShell.Body>
          {runsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : runs && runs.length > 0 ? (
            <div className="space-y-2">
              {runs.map((run) => (
                <div key={run.id} className="rounded-lg border p-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <Badge variant={run.status === 'success' ? 'default' : 'secondary'}>
                      {run.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatDate(run.started_at)}
                    </span>
                  </div>
                  {run.error_message ? (
                    <p className="mt-2 text-xs text-destructive">{run.error_message}</p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">아직 실행 이력이 없습니다.</p>
          )}
        </DialogShell.Body>
      </DialogShell>

      <DeleteConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="스케줄 삭제"
        description="이 스케줄을 삭제합니다. 이미 생성된 결과 대화는 삭제되지 않습니다."
        cancelLabel="취소"
        confirmLabel="삭제"
        isPending={deleteTrigger.isPending}
        onConfirm={() => {
          if (!deleteTarget) return
          deleteTrigger.mutate(deleteTarget.id, { onSuccess: () => setDeleteTarget(null) })
        }}
      />
    </div>
  )
}
