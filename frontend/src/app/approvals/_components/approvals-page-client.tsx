'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useFormatter, useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { ApiErrorState } from '@/components/shared/api-error-state'
import { PageShell } from '@/components/shared/page-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api/client'
import {
  useApprovals,
  useDecideApproval,
  useEmergencyStops,
  useRunStatus,
} from '@/lib/hooks/use-approvals'
import type {
  ApprovalAction,
  ApprovalDecisionType,
  ApprovalView,
  EmergencyStopView,
  InboxDecision,
  RunState,
} from '@/lib/types/approval'
import { ReleaseStopDialog } from './release-stop-dialog'

export type ApprovalTab = 'pending' | 'done' | 'runs' | 'stops'
const TABS: readonly ApprovalTab[] = ['pending', 'done', 'runs', 'stops']
const RUN_STATES: readonly RunState[] = ['running', 'waiting', 'failed']

/** 승인함 (plan 17.2): requests from every run, the run status tab and emergency stops. */
export function ApprovalsPageClient({ initialTab = 'pending' }: { initialTab?: ApprovalTab }) {
  const t = useTranslations('approvals')
  const [tab, setTab] = useState<ApprovalTab>(initialTab)
  return (
    <PageShell title={t('title')} description={t('description')}>
      <Tabs value={tab} onValueChange={(value) => setTab(value as ApprovalTab)}>
        <LineTabsList aria-label={t('title')}>
          {TABS.map((value) => (
            <LineTabsTrigger key={value} value={value}>
              {t(`tabs.${value}`)}
            </LineTabsTrigger>
          ))}
        </LineTabsList>
      </Tabs>
      {tab === 'pending' || tab === 'done' ? <RequestList status={tab} /> : null}
      {tab === 'runs' ? <RunStatusList /> : null}
      {tab === 'stops' ? <StopList /> : null}
    </PageShell>
  )
}

function RequestList({ status }: { status: 'pending' | 'done' }) {
  const t = useTranslations('approvals')
  const page = useApprovals(status)
  if (page.isLoading) return <Skeleton className="h-24 w-full" />
  if (!page.data) return <ApiErrorState error={page.error} onRetry={() => void page.refetch()} />
  if (page.data.items.length === 0)
    return <p className="text-sm text-muted-foreground">{t(`empty.${status}`)}</p>
  return (
    <ul className="space-y-3">
      {page.data.items.map((request) => (
        <li key={request.id}>
          <RequestCard request={request} />
        </li>
      ))}
    </ul>
  )
}

function argsText(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value)
  }
}

function RequestCard({ request }: { request: ApprovalView }) {
  const t = useTranslations('approvals')
  const format = useFormatter()
  const decide = useDecideApproval()
  const pending = request.status === 'pending'
  const [choices, setChoices] = useState<ApprovalDecisionType[]>(() =>
    request.actions.map((action) =>
      action.allowed_decisions.includes('approve') ? 'approve' : 'reject',
    ),
  )
  const [edits, setEdits] = useState<string[]>(() => request.actions.map((a) => argsText(a.args)))

  const submit = (decisions: InboxDecision[]) =>
    decide.mutate(
      { id: request.id, decisions },
      {
        onSuccess: () => toast.success(t('toast.decided')),
        onError: (error) => {
          const code = error instanceof ApiError ? error.code : ''
          const known = ['approval_stale', 'approval_expired', 'agent_emergency_stopped']
          toast.error(t(known.includes(code) ? `errors.${code}` : 'errors.generic'))
        },
      },
    )

  const built = (): InboxDecision[] | null => {
    const decisions: InboxDecision[] = []
    for (const [index, choice] of choices.entries()) {
      if (choice !== 'edit') {
        decisions.push({ type: choice })
        continue
      }
      try {
        const parsed: unknown = JSON.parse(edits[index] ?? '{}')
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
        decisions.push({ type: 'edit', edited_action: { args: parsed as Record<string, unknown> } })
      } catch {
        return null
      }
    }
    return decisions
  }

  return (
    <SettingsSectionCard title={request.agent_name || t('unknownAgent')}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">{t(`source.${request.source}`)}</Badge>
        <Badge variant={pending ? 'secondary' : 'outline'}>{t(`status.${request.status}`)}</Badge>
        {pending ? (
          <span>{t('expires', { when: format.relativeTime(new Date(request.expires_at)) })}</span>
        ) : request.decided_at ? (
          <span>{format.relativeTime(new Date(request.decided_at))}</span>
        ) : null}
        {request.attempt > 1 ? <span>{t('attempt', { n: request.attempt })}</span> : null}
        {request.reason ? <span>{t(`reasons.${request.reason}`)}</span> : null}
      </div>
      <ol className="space-y-3">
        {request.actions.map((action, index) => (
          <ActionRow
            key={`${request.id}-${index}`}
            action={action}
            pending={pending}
            choice={choices[index] ?? 'reject'}
            onChoice={(choice) =>
              setChoices((current) => current.map((c, i) => (i === index ? choice : c)))
            }
            edit={edits[index] ?? ''}
            onEdit={(text) =>
              setEdits((current) => current.map((e, i) => (i === index ? text : e)))
            }
          />
        ))}
      </ol>
      {pending ? (
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={decide.isPending}
            onClick={() => submit(request.actions.map(() => ({ type: 'reject' })))}
          >
            {t('rejectAll')}
          </Button>
          <Button
            size="sm"
            disabled={decide.isPending}
            onClick={() => {
              const decisions = built()
              if (decisions === null) toast.error(t('errors.invalidArgs'))
              else submit(decisions)
            }}
          >
            {t('submit')}
          </Button>
        </div>
      ) : null}
      <div className="mt-3 text-xs">
        <Link className="text-muted-foreground underline" href={conversationHref(request)}>
          {t('openConversation')}
        </Link>
      </div>
    </SettingsSectionCard>
  )
}

function conversationHref(request: ApprovalView): string {
  return request.agent_id
    ? `/agents/${request.agent_id}/conversations/${request.conversation_id}`
    : '/'
}

const DECISION_ORDER: readonly ApprovalDecisionType[] = ['approve', 'edit', 'reject']

function ActionRow({
  action,
  pending,
  choice,
  onChoice,
  edit,
  onEdit,
}: {
  action: ApprovalAction
  pending: boolean
  choice: ApprovalDecisionType
  onChoice: (choice: ApprovalDecisionType) => void
  edit: string
  onEdit: (text: string) => void
}) {
  const t = useTranslations('approvals')
  const allowed = DECISION_ORDER.filter((d) => action.allowed_decisions.includes(d))
  return (
    <li className="rounded-lg border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="text-sm font-medium">{action.name}</code>
        {pending ? (
          <div
            className="flex gap-1"
            role="group"
            aria-label={t('decisionFor', { tool: action.name })}
          >
            {allowed.map((decision) => (
              <Button
                key={decision}
                size="xs"
                variant={choice === decision ? 'default' : 'outline'}
                aria-pressed={choice === decision}
                onClick={() => onChoice(decision)}
              >
                {t(`decision.${decision}`)}
              </Button>
            ))}
          </div>
        ) : null}
      </div>
      {action.description ? (
        <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">
          {action.description}
        </p>
      ) : null}
      {pending && choice === 'edit' ? (
        <Textarea
          className="mt-2 font-mono text-xs"
          aria-label={t('editArgs', { tool: action.name })}
          value={edit}
          onChange={(event) => onEdit(event.target.value)}
          rows={6}
        />
      ) : (
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-muted p-2 text-xs">
          {argsText(action.args)}
        </pre>
      )}
    </li>
  )
}

function RunStatusList() {
  const t = useTranslations('approvals')
  const [state, setState] = useState<RunState>('running')
  const runs = useRunStatus(state)
  const format = useFormatter()
  return (
    <SettingsSectionCard title={t('tabs.runs')}>
      <Tabs value={state} onValueChange={(value) => setState(value as RunState)} className="mb-3">
        <LineTabsList aria-label={t('tabs.runs')}>
          {RUN_STATES.map((value) => (
            <LineTabsTrigger key={value} value={value}>
              {t(`runs.${value}`)}
            </LineTabsTrigger>
          ))}
        </LineTabsList>
      </Tabs>
      {runs.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !runs.data ? (
        <ApiErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      ) : runs.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t(`runs.empty.${state}`)}</p>
      ) : (
        <ul className="divide-y divide-border">
          {runs.data.items.map((run) => (
            <li key={run.run_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <div className="min-w-0 text-sm">
                <span className="font-medium">{run.agent_name || t('unknownAgent')}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  {run.mine ? t('runs.mine') : t('runs.ofMyAgent')}
                  {run.error_code ? ` · ${run.error_code}` : ''}
                </span>
              </div>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span>{format.relativeTime(new Date(run.created_at))}</span>
                {run.conversation_id && run.agent_id ? (
                  <Link
                    className="underline"
                    href={`/agents/${run.agent_id}/conversations/${run.conversation_id}`}
                  >
                    {t('openConversation')}
                  </Link>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </SettingsSectionCard>
  )
}

function StopList() {
  const t = useTranslations('approvals')
  const stops = useEmergencyStops(true)
  const [releasing, setReleasing] = useState<EmergencyStopView | null>(null)
  return (
    <SettingsSectionCard title={t('tabs.stops')}>
      {stops.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !stops.data ? (
        <ApiErrorState error={stops.error} onRetry={() => void stops.refetch()} />
      ) : stops.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('stops.empty')}</p>
      ) : (
        <ul className="divide-y divide-border">
          {stops.data.items.map((stop) => (
            <li key={stop.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 space-y-1 text-sm">
                <p className="font-medium">
                  {t(`stops.scope.${stop.scope_type}`)} · {stop.scope_name}
                </p>
                <p className="text-xs text-muted-foreground">&ldquo;{stop.reason}&rdquo;</p>
                <ul className="text-xs text-muted-foreground">
                  <li>{t('stops.state.blocked')}</li>
                  <li>
                    {t('stops.state.runs', {
                      confirmed: stop.state.runs_cancel_confirmed,
                      total: stop.state.runs_cancel_requested,
                    })}
                  </li>
                  <li>
                    {t('stops.state.rest', {
                      schedules: stop.state.schedules_paused,
                      deployments: stop.state.deployments_disabled,
                      approvals: stop.state.approvals_canceled,
                    })}
                  </li>
                  <li>{t('stops.state.sideEffects')}</li>
                </ul>
              </div>
              <Button size="sm" variant="outline" onClick={() => setReleasing(stop)}>
                {t('stops.release')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ReleaseStopDialog stop={releasing} onClose={() => setReleasing(null)} />
    </SettingsSectionCard>
  )
}
