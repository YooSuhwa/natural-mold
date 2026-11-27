'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { TargetIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DialogShell } from '@/components/shared/dialog-shell'
import { ApiError, API_BASE } from '@/lib/api/client'
import { useConversationGoal, goalEvidenceDownloadPath } from '@/lib/hooks/use-conversation-goal'
import type { ConversationGoal, GoalAction, GoalCriterion } from '@/lib/types/conversation-goal'
import { GoalCriterionForm, buildCriterion, criterionDraft } from './goal-criterion-form'

function passedCheckLabel(criterion: GoalCriterion) {
  if (criterion.kind !== 'csv_table') return 'passed'
  return criterion.reference_rows?.length ? 'tableReferenceMatched' : 'tableRulesPassed'
}

type Draft = { revision: number; title: string; criteria: ReturnType<typeof criterionDraft>[] }

function fromGoal(goal: ConversationGoal | null, additionalCriterion?: GoalCriterion): Draft {
  const criteria = goal?.criteria.map(criterionDraft) ?? []
  if (additionalCriterion) criteria.push(criterionDraft(additionalCriterion))
  if (criteria.length === 0) criteria.push(criterionDraft())
  return { revision: goal?.revision ?? 0, title: goal?.title ?? '', criteria }
}

interface GoalCardProps {
  conversationId: string
  onCreating?: () => void
  onCreated?: (conversationId: string) => void
}

export function ConversationGoalCard({ conversationId, onCreating, onCreated }: GoalCardProps) {
  const t = useTranslations('chat.goal')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [validationError, setValidationError] = useState(false)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const formId = useId()
  const detailsRef = useRef<HTMLDetailsElement>(null)
  const { query, mutation } = useConversationGoal(conversationId, () => {
    setDraft(null)
    setConfirmCancel(false)
  })
  const goal = query.data?.goal ?? null
  const enabled = !!query.data?.enabled && !query.isError
  const goalId = goal?.id
  useEffect(() => {
    const openLinkedGoal = () => {
      if (window.location.hash === '#conversation-goal' && detailsRef.current) {
        detailsRef.current.open = true
      }
    }
    openLinkedGoal()
    window.addEventListener('hashchange', openLinkedGoal)
    return () => window.removeEventListener('hashchange', openLinkedGoal)
  }, [goalId])
  const hasCriterionRoom = !draft || draft.criteria.length < 12
  const acceptsSteer =
    hasCriterionRoom && enabled && goal?.status === 'active' && goal.criteria.length < 12
  useEffect(() => {
    window.dispatchEvent(new Event('moldy:goal-editor-change'))
    return () => {
      window.dispatchEvent(new Event('moldy:goal-editor-change'))
    }
  }, [conversationId, acceptsSteer])
  useEffect(() => {
    if (!acceptsSteer || !goal) return
    const review = (event: Event) => {
      if (!(event instanceof CustomEvent)) return
      const detail: unknown = event.detail
      if (
        typeof detail !== 'object' ||
        detail === null ||
        !('conversationId' in detail) ||
        detail.conversationId !== conversationId ||
        !('text' in detail) ||
        typeof detail.text !== 'string' ||
        !detail.text.trim()
      )
        return
      const addition: GoalCriterion = { kind: 'manual', description: detail.text.trim() }
      setValidationError(false)
      setDraft((existing) => {
        if (!existing) return fromGoal(goal, addition)
        if (existing.criteria.length >= 12) return existing
        return { ...existing, criteria: [...existing.criteria, criterionDraft(addition)] }
      })
    }
    window.addEventListener('moldy:review-steer-criterion', review)
    return () => window.removeEventListener('moldy:review-steer-criterion', review)
  }, [conversationId, acceptsSteer, goal])

  if (!query.data || query.isError || (!enabled && !goal)) return null
  const errorCode = mutation.error instanceof ApiError ? mutation.error.code : null
  let mutationError = mutation.isError ? t('saveError') : null
  if (errorCode === 'goal_revision_conflict') mutationError = t('conflict')
  else if (errorCode && t.has(`reasons.${errorCode}`)) mutationError = t(`reasons.${errorCode}`)
  const draftStale = draft !== null && draft.revision !== (goal?.revision ?? 0)
  const openEditor = () => {
    mutation.reset()
    setValidationError(false)
    setDraft(fromGoal(goal))
  }
  const doAction = (action: GoalAction) => {
    if (enabled && goal) mutation.mutate({ action, expected_revision: goal.revision })
  }
  const save = () => {
    if (!draft || !enabled || draftStale || mutation.isPending) return
    const criteria = draft.criteria.map(buildCriterion)
    const title = draft.title.trim()
    if (
      !title ||
      !criteria.length ||
      criteria.length > 12 ||
      criteria.some((entry) => entry === null)
    ) {
      setValidationError(true)
      return
    }
    const creating = draft.revision === 0
    if (creating) onCreating?.()
    mutation.mutate(
      {
        save: {
          expected_revision: draft.revision,
          title,
          criteria: criteria.filter((entry) => entry !== null),
        },
      },
      {
        onSuccess: (result) => {
          if (creating && result.goal) onCreated?.(conversationId)
        },
      },
    )
  }

  return (
    <aside
      id="conversation-goal"
      data-steer-conversation={acceptsSteer ? conversationId : undefined}
      aria-label={t('heading')}
      className="mx-auto w-full max-w-3xl px-3 py-1"
    >
      {goal ? (
        <details
          className="moldy-muted-panel max-h-64 overflow-y-auto px-3 py-2 text-sm"
          ref={detailsRef}
        >
          <summary className="cursor-pointer break-words font-medium">
            <TargetIcon className="mx-1 inline size-4 text-primary-strong" aria-hidden="true" />
            {goal.title}
            <span className="ml-2 text-xs text-muted-foreground">
              {t(`statuses.${goal.status}`)}
            </span>
          </summary>
          <p className="my-2 text-xs text-muted-foreground">
            {t('versionAndRuns', { version: goal.version, count: goal.auto_followups })}
          </p>
          {enabled && goal.status === 'active' && !goal.last_evidence && (
            <p className="mb-2 text-xs text-muted-foreground">{t('startHint')}</p>
          )}
          <ol className="list-decimal space-y-2 pl-5">
            {goal.criteria.map((criterion, index) => {
              const check = goal.last_evidence?.checks.find(
                (entry) => entry.criterion_index === index,
              )
              const reason = check?.reason
              return (
                <li key={index} className="break-words">
                  <p>
                    {criterion.kind === 'manual'
                      ? criterion.description
                      : t(`kinds.${criterion.kind}`) + ' · ' + criterion.logical_path}
                  </p>
                  {criterion.kind === 'csv_table' && (
                    <div className="text-xs text-muted-foreground">
                      <p>
                        {t('csvSummary', {
                          columns: criterion.required_columns.join(', '),
                          key: criterion.unique_by,
                          count: criterion.minimum_rows,
                        })}
                      </p>
                      {Object.entries(criterion.column_equals).map(([name, value]) => (
                        <p key={name}>{t('equals', { name, value })}</p>
                      ))}
                      {!!criterion.reference_rows?.length && (
                        <p>{t('references', { count: criterion.reference_rows.length })}</p>
                      )}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {check?.passed
                      ? t(passedCheckLabel(criterion))
                      : reason && t.has(`checks.${reason}`)
                        ? t(`checks.${reason}`)
                        : check
                          ? t('unmet')
                          : t('awaitingCheck')}
                  </p>
                  {check?.passed &&
                    check.artifact_id &&
                    check.version_id &&
                    check.logical_path &&
                    check.version_number !== null && (
                      <a
                        className="text-xs text-primary-strong underline"
                        href={
                          API_BASE +
                          goalEvidenceDownloadPath(
                            conversationId,
                            check.artifact_id,
                            check.version_id,
                          )
                        }
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {t('evidence', { path: check.logical_path, version: check.version_number })}
                      </a>
                    )}
                </li>
              )
            })}
          </ol>
          {goal.blocked_reason && (
            <p className="mt-2 text-sm text-status-warn">
              {t.has(`reasons.${goal.blocked_reason}`)
                ? t(`reasons.${goal.blocked_reason}`)
                : t('blockedFallback')}
            </p>
          )}
          {enabled && (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={mutation.isPending}
                onClick={openEditor}
              >
                {t('edit')}
              </Button>
              {goal.status === 'active' && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending}
                  onClick={() => doAction('pause')}
                >
                  {t('pause')}
                </Button>
              )}
              {(goal.status === 'paused' || goal.status === 'blocked') && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={mutation.isPending}
                  onClick={() => doAction('resume')}
                >
                  {t('resume')}
                </Button>
              )}
              {goal.status !== 'completed' &&
                goal.status !== 'canceled' &&
                (confirmCancel ? (
                  <>
                    <span>{t('confirmCancel')}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      disabled={mutation.isPending}
                      onClick={() => doAction('cancel')}
                    >
                      {t('cancelGoal')}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirmCancel(false)}
                    >
                      {t('keepGoal')}
                    </Button>
                  </>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={mutation.isPending}
                    onClick={() => setConfirmCancel(true)}
                  >
                    {t('cancelGoal')}
                  </Button>
                ))}
            </div>
          )}
          {!enabled && <p className="mt-2 text-xs text-muted-foreground">{t('readOnly')}</p>}
        </details>
      ) : (
        <Button type="button" size="sm" variant="ghost" onClick={openEditor}>
          <TargetIcon aria-hidden="true" />
          {t('create')}
        </Button>
      )}
      {!draft && mutationError && (
        <p role="alert" className="text-sm text-status-danger">
          {mutationError}
        </p>
      )}
      {enabled && draft && (
        <DialogShell
          open
          onOpenChange={(open) => {
            if (!open && !mutation.isPending) setDraft(null)
          }}
        >
          <DialogShell.Header title={goal ? t('edit') : t('create')} />
          <DialogShell.Body>
            <form
              id={formId}
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault()
                save()
              }}
            >
              <label className="grid gap-1">
                {t('title')}
                <Input
                  required
                  maxLength={500}
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
              </label>
              {draft.criteria.map((criterion, index) => (
                <GoalCriterionForm
                  key={criterion.id}
                  index={index}
                  draft={criterion}
                  canRemove={draft.criteria.length > 1}
                  onChange={(value) =>
                    setDraft({
                      ...draft,
                      criteria: draft.criteria.map((item) => (item.id === value.id ? value : item)),
                    })
                  }
                  onRemove={() =>
                    setDraft({
                      ...draft,
                      criteria: draft.criteria.filter((item) => item.id !== criterion.id),
                    })
                  }
                />
              ))}
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={draft.criteria.length >= 12}
                onClick={() =>
                  setDraft({ ...draft, criteria: [...draft.criteria, criterionDraft()] })
                }
              >
                {t('addCondition')}
              </Button>
              {(validationError || mutationError || draftStale) && (
                <p role="alert" className="text-sm text-status-danger">
                  {draftStale ? t('conflict') : mutationError || t('invalid')}
                </p>
              )}
            </form>
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button
              type="button"
              variant="outline"
              disabled={mutation.isPending}
              onClick={() => setDraft(null)}
            >
              {t('close')}
            </Button>
            {draftStale && (
              <Button type="button" onClick={openEditor}>
                {t('reload')}
              </Button>
            )}
            <Button type="submit" form={formId} disabled={mutation.isPending || draftStale}>
              {t('save')}
            </Button>
          </DialogShell.Footer>
        </DialogShell>
      )}
    </aside>
  )
}
