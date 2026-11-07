'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { CheckCheckIcon, CheckIcon, Loader2Icon, PencilIcon, XIcon } from 'lucide-react'
import { toast } from 'sonner'

import { ResourceListState } from '@/components/shared/resource-list-state'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/textarea'
import {
  useApproveMemoryProposal,
  useBulkApproveMemoryProposals,
  useEditAndApproveMemoryProposal,
  useMemoryProposals,
  useRejectMemoryProposal,
} from '@/lib/hooks/use-memory'
import type { MemoryProposal, MemoryProposalSource } from '@/lib/types'
import type { MemoryAgentOption } from './memory-form-fields'

/** How a proposal is labelled and grouped; `profile` splits into add and remove. */
const CHANGE_KINDS = [
  'create',
  'merge',
  'supersede',
  'update',
  'expire',
  'revoke',
  'pin',
  'profileAdd',
  'profileRemove',
  'triggerMemo',
  'unknown',
] as const
type ChangeKind = (typeof CHANGE_KINDS)[number]

const BULK_REASONS = new Set([
  'contradiction',
  'marked',
  'replaces_confirmed',
  'not_pending',
  'not_found',
  'memory_proposal_stale',
])

function changeKind(proposal: MemoryProposal): ChangeKind {
  if (proposal.action === 'profile') {
    const op = proposal.profile_change?.op
    if (op === 'replace_section') return 'triggerMemo'
    return op === 'remove' ? 'profileRemove' : 'profileAdd'
  }
  return (CHANGE_KINDS as readonly string[]).includes(proposal.action)
    ? (proposal.action as ChangeKind)
    : 'unknown'
}

/** Truthy flag keys, minus what the sensitivity and origin badges already show. */
function proposalFlags(proposal: MemoryProposal): string[] {
  return Object.entries(proposal.flags ?? {})
    .filter(
      ([key, value]) =>
        Boolean(value) &&
        key !== proposal.sensitivity &&
        !(key === 'consolidation' && proposal.origin === 'consolidation') &&
        key !== 'trigger_memo',
    )
    .map(([key]) => key)
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

interface SkippedRow {
  id: string
  reason: string
  content: string
}

/**
 * 승인 대기 (plan 15, 5.9): memory and profile proposals with a before/after view. Only
 * unmarked proposals (`bulk_block === null`) can be approved in bulk (5.6).
 */
export function MemoryApprovalsTab({ agents }: { agents: MemoryAgentOption[] }) {
  const t = useTranslations('appSettings.memory.approvals')
  const proposals = useMemoryProposals('pending')
  const bulk = useBulkApproveMemoryProposals()
  const rows = proposals.data ?? []
  const [selected, setSelected] = useState<string[]>([])
  const [skipped, setSkipped] = useState<SkippedRow[]>([])

  const eligible = rows.filter((row) => row.bulk_block === null).map((row) => row.id)
  // Derived, so a decided or refetched-away proposal never stays selected.
  const chosen = selected.filter((id) => eligible.includes(id))
  const excluded = rows.length - eligible.length
  const allChosen = eligible.length > 0 && chosen.length === eligible.length
  const agentName = new Map(agents.map((agent) => [agent.id, agent.name]))
  const groups = CHANGE_KINDS.map((kind) => ({
    kind,
    items: rows.filter((row) => changeKind(row) === kind),
  })).filter((group) => group.items.length > 0)

  function bulkReason(reason: string): string {
    return BULK_REASONS.has(reason) ? t(`bulkBlock.${reason}`) : t('bulkBlock.other', { reason })
  }

  async function handleBulk() {
    const contents = new Map(rows.map((row) => [row.id, row.content]))
    try {
      const result = await bulk.mutateAsync(chosen)
      setSelected([])
      setSkipped(
        result.skipped.map((skip) => ({ ...skip, content: contents.get(skip.id) ?? skip.id })),
      )
      toast.success(t('bulk.done', { count: result.approved.length }))
    } catch (error) {
      toast.error(errorMessage(error, t('bulk.failed')))
    }
  }

  return (
    <SettingsSectionCard
      title={
        <span className="flex items-center gap-2">
          <CheckCheckIcon className="size-4" aria-hidden />
          {t('title')}
        </span>
      }
      description={t('description')}
    >
      {!proposals.isLoading && !proposals.isError && rows.length > 0 ? (
        <div className="space-y-4" data-testid="memory-approvals">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm text-foreground">
              <Checkbox
                aria-label={t('bulk.selectAll')}
                checked={allChosen}
                disabled={eligible.length === 0}
                onCheckedChange={(value) => setSelected(value ? eligible : [])}
                data-testid="memory-approvals-select-all"
              />
              {t('bulk.selectAll')}
            </label>
            <Button
              type="button"
              onClick={() => void handleBulk()}
              disabled={chosen.length === 0 || bulk.isPending}
              data-testid="memory-approvals-bulk-submit"
            >
              {bulk.isPending ? (
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
              ) : (
                <CheckCheckIcon className="size-4" aria-hidden />
              )}
              {t('bulk.submit', { count: chosen.length })}
            </Button>
          </div>
          {excluded > 0 ? (
            <p className="text-xs text-muted-foreground" data-testid="memory-approvals-excluded">
              {t('bulk.excluded', { count: excluded })}
            </p>
          ) : null}
          {skipped.length > 0 ? (
            <div
              role="status"
              className="space-y-1 rounded-lg border border-border/70 bg-muted/40 p-3 text-sm"
              data-testid="memory-approvals-skipped"
            >
              <p className="font-medium text-foreground">
                {t('bulk.skippedTitle', { count: skipped.length })}
              </p>
              <ul className="space-y-1 text-xs text-muted-foreground">
                {skipped.map((skip) => (
                  <li key={skip.id} className="flex flex-wrap gap-x-2">
                    <span className="text-foreground">{skip.content}</span>
                    <span>{bulkReason(skip.reason)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {groups.map((group) => (
            <div
              key={group.kind}
              className="space-y-2"
              data-testid={`memory-approvals-group-${group.kind}`}
            >
              <h3 className="text-sm font-medium text-foreground">
                {t(`action.${group.kind}`)}{' '}
                <span className="text-muted-foreground">({group.items.length})</span>
              </h3>
              {group.items.map((proposal) => (
                <ProposalItem
                  key={proposal.id}
                  proposal={proposal}
                  agentName={proposal.agent_id ? agentName.get(proposal.agent_id) : undefined}
                  selected={chosen.includes(proposal.id)}
                  blockedReason={
                    proposal.bulk_block === null ? null : bulkReason(proposal.bulk_block)
                  }
                  onSelect={(checked) =>
                    setSelected((prev) =>
                      checked
                        ? [...prev.filter((id) => id !== proposal.id), proposal.id]
                        : prev.filter((id) => id !== proposal.id),
                    )
                  }
                />
              ))}
            </div>
          ))}
        </div>
      ) : (
        <ResourceListState
          loading={proposals.isLoading}
          error={proposals.isError}
          onRetry={() => void proposals.refetch()}
          retryLabel={t('retry')}
          skeleton={<p className="text-sm text-muted-foreground">{t('loading')}</p>}
          emptyTitle={t('emptyTitle')}
          emptyDescription={t('emptyDescription')}
          filteredEmptyTitle={t('emptyTitle')}
        />
      )}
    </SettingsSectionCard>
  )
}

function ProposalItem({
  proposal,
  agentName,
  selected,
  blockedReason,
  onSelect,
}: {
  proposal: MemoryProposal
  agentName: string | undefined
  selected: boolean
  blockedReason: string | null
  onSelect: (checked: boolean) => void
}) {
  const t = useTranslations('appSettings.memory.approvals')
  const tMemory = useTranslations('appSettings.memory')
  const approve = useApproveMemoryProposal()
  const reject = useRejectMemoryProposal()
  const editAndApprove = useEditAndApproveMemoryProposal()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(proposal.content)
  const pending = approve.isPending || reject.isPending || editAndApprove.isPending
  const kind = changeKind(proposal)
  // The server refuses edits of consolidation proposals (MEMORY_PROPOSAL_NOT_EDITABLE).
  const editable = proposal.action === 'create'
  const flags = proposalFlags(proposal)

  async function run(action: () => Promise<unknown>, done: string) {
    try {
      await action()
      setEditing(false)
      toast.success(done)
    } catch (error) {
      // A stale proposal answers with the server's reason; the hook refetches the queue.
      toast.error(errorMessage(error, t('toast.failed')))
    }
  }

  return (
    <article
      className="space-y-3 rounded-lg border border-border/70 bg-background p-3"
      data-testid={`memory-proposal-${proposal.id}`}
    >
      <div className="flex flex-wrap items-start gap-3">
        <Checkbox
          aria-label={t('bulk.selectItem')}
          checked={selected}
          disabled={blockedReason !== null}
          onCheckedChange={(value) => onSelect(Boolean(value))}
          className="mt-1"
          data-testid={`memory-proposal-select-${proposal.id}`}
        />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{t(`action.${kind}`)}</Badge>
            <Badge variant="outline">{tMemory(`scope.${proposal.scope}`)}</Badge>
            {agentName ? <Badge variant="outline">{agentName}</Badge> : null}
            <Badge variant="outline">{tMemory(`origin.${proposal.origin}`)}</Badge>
            {proposal.subject === 'other' ? (
              <Badge variant="outline">{t('subjectOther')}</Badge>
            ) : null}
            {proposal.sensitivity !== 'normal' ? (
              <Badge variant="destructive">{tMemory(`sensitivity.${proposal.sensitivity}`)}</Badge>
            ) : null}
            {flags.map((flag) => (
              <Badge key={flag} variant="outline" data-testid={`memory-proposal-flag-${flag}`}>
                {tMemory.has(`flags.${flag}`)
                  ? tMemory(`flags.${flag}`)
                  : tMemory('flags.unknown', { flag })}
              </Badge>
            ))}
          </div>
          {editing ? (
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="min-h-20"
              aria-label={t('editContent')}
              data-testid={`memory-proposal-edit-content-${proposal.id}`}
            />
          ) : (
            <ProposalDiff proposal={proposal} kind={kind} />
          )}
          {proposal.reason ? (
            <p className="text-xs text-muted-foreground">
              {t('reason', { reason: proposal.reason })}
            </p>
          ) : null}
          {blockedReason ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid={`memory-proposal-blocked-${proposal.id}`}
            >
              {t('bulk.blocked', { reason: blockedReason })}
            </p>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        {editing ? (
          <>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              {t('cancelEdit')}
            </Button>
            <Button
              type="button"
              disabled={pending || !draft.trim()}
              onClick={() =>
                void run(
                  () =>
                    editAndApprove.mutateAsync({
                      id: proposal.id,
                      data: { content: draft.trim(), reason: proposal.reason },
                    }),
                  t('toast.approved'),
                )
              }
              data-testid={`memory-proposal-save-edit-${proposal.id}`}
            >
              <CheckIcon className="size-4" aria-hidden />
              {t('saveEdit')}
            </Button>
          </>
        ) : (
          <>
            {editable ? (
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
                onClick={() => {
                  setDraft(proposal.content)
                  setEditing(true)
                }}
                data-testid={`memory-proposal-edit-${proposal.id}`}
              >
                <PencilIcon className="size-4" aria-hidden />
                {t('editApprove')}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={() => void run(() => reject.mutateAsync(proposal.id), t('toast.rejected'))}
              data-testid={`memory-proposal-reject-${proposal.id}`}
            >
              <XIcon className="size-4" aria-hidden />
              {t('reject')}
            </Button>
            <Button
              type="button"
              disabled={pending}
              onClick={() => void run(() => approve.mutateAsync(proposal.id), t('toast.approved'))}
              data-testid={`memory-proposal-approve-${proposal.id}`}
            >
              {approve.isPending ? (
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
              ) : (
                <CheckIcon className="size-4" aria-hidden />
              )}
              {t('approve')}
            </Button>
          </>
        )}
      </div>
    </article>
  )
}

function LineList({ lines }: { lines?: string[] }) {
  const items = Array.isArray(lines) ? lines.filter((line) => typeof line === 'string') : []
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm leading-6 text-foreground">
      {items.map((line, index) => (
        <li key={`${index}-${line}`}>{line}</li>
      ))}
    </ul>
  )
}

/** Before (`sources`, as they are now) and after (`content` / `profile_change`). */
function ProposalDiff({ proposal, kind }: { proposal: MemoryProposal; kind: ChangeKind }) {
  const t = useTranslations('appSettings.memory.approvals')
  const sources = proposal.sources
  const change = proposal.profile_change
  const target = change ? t(`profileTarget.${change.kind}`) : t('profileTarget.user_profile')

  const struck = (text: string) => (
    <del className="whitespace-pre-wrap text-sm leading-6 text-muted-foreground">{text}</del>
  )

  const sourceList = <SourceList sources={sources} />
  const contentText = <ContentText text={proposal.content} />

  function sides(): { before: ReactNode; after: ReactNode } {
    switch (kind) {
      case 'merge':
        return {
          before: sourceList,
          after: contentText,
        }
      case 'supersede':
      case 'update':
        return {
          before: sources[0] ? struck(sources[0].content) : <Missing />,
          after: contentText,
        }
      case 'expire':
      case 'revoke':
      case 'pin':
        return {
          before: sourceList,
          after: <p className="text-sm text-foreground">{t(`outcome.${kind}`)}</p>,
        }
      case 'profileAdd':
        return {
          before: null,
          after: (
            <div className="space-y-2">
              <ContentText text={change?.text ?? proposal.content} />
              <p className="text-xs text-muted-foreground">{t('outcome.profileAdd', { target })}</p>
              {sources.length > 0 ? (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">{t('sources')}</p>
                  {sourceList}
                </div>
              ) : null}
            </div>
          ),
        }
      case 'triggerMemo':
        return {
          before: <p className="text-sm text-muted-foreground">{t('outcome.triggerMemoBefore')}</p>,
          after: (
            <div className="space-y-2" data-testid={`memory-proposal-trigger-memo-${proposal.id}`}>
              <p className="text-xs font-medium text-muted-foreground">{t('triggerRecent')}</p>
              <LineList lines={change?.recent} />
              {change?.tips && change.tips.length > 0 ? (
                <>
                  <p className="text-xs font-medium text-muted-foreground">{t('triggerTips')}</p>
                  <LineList lines={change.tips} />
                </>
              ) : null}
            </div>
          ),
        }
      case 'profileRemove':
        return {
          before: struck(change?.text ?? proposal.content),
          after: (
            <p className="text-sm text-foreground">{t('outcome.profileRemove', { target })}</p>
          ),
        }
      default:
        return { before: null, after: contentText }
    }
  }
  const { before, after } = sides()

  return (
    <div className="grid gap-2 md:grid-cols-2">
      {before ? (
        <div
          className="space-y-1 rounded-md border border-border/70 bg-muted/40 p-2"
          data-testid={`memory-proposal-before-${proposal.id}`}
        >
          <p className="text-xs font-medium text-muted-foreground">{t('before')}</p>
          {before}
        </div>
      ) : null}
      <div
        className="space-y-1 rounded-md border border-border/70 p-2"
        data-testid={`memory-proposal-after-${proposal.id}`}
      >
        <p className="text-xs font-medium text-muted-foreground">{t('after')}</p>
        {after}
      </div>
    </div>
  )
}

function ContentText({ text }: { text: string }) {
  return <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">{text}</p>
}

function Missing() {
  const t = useTranslations('appSettings.memory.approvals')
  return <p className="text-xs text-muted-foreground">{t('missingSource')}</p>
}

function SourceList({ sources }: { sources: MemoryProposalSource[] }) {
  const t = useTranslations('appSettings.memory.approvals.provenance')
  if (sources.length === 0) return <Missing />
  return (
    <ul className="space-y-1">
      {sources.map((source) => (
        <li key={source.id} className="space-y-0.5">
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">{source.content}</p>
          <p className="text-xs text-muted-foreground">{t(source.provenance)}</p>
        </li>
      ))}
    </ul>
  )
}
