'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import {
  AlertTriangle,
  CheckCircle2,
  FileCode2,
  Loader2,
  MessageSquareText,
  Sparkles,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile } from '@/components/shared/icon'
import { ApiError } from '@/lib/api/client'
import { useConfirmSkillBuilderSession, useStartSkillBuilder } from '@/lib/hooks/use-skill-builder'
import { SkillBuilderPreview } from './skill-builder-preview'
import type { SkillBuilderMode, SkillBuilderSession } from '@/lib/types'

type SkillBuilderOpenTab = 'content' | 'evaluation'

interface SkillBuilderDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly mode?: SkillBuilderMode
  readonly sourceSkillId?: string | null
  readonly initialRequest?: string
  readonly onCreated?: (skillId: string, options: { readonly openTab: SkillBuilderOpenTab }) => void
}

export function SkillBuilderDialog({
  open,
  onOpenChange,
  mode = 'create',
  sourceSkillId = null,
  initialRequest = '',
  onCreated,
}: SkillBuilderDialogProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      {open ? (
        <SkillBuilderDialogInner
          key={`${mode}-${sourceSkillId ?? 'new'}-${initialRequest}`}
          mode={mode}
          sourceSkillId={sourceSkillId}
          initialRequest={initialRequest}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      ) : null}
    </DialogShell>
  )
}

function SkillBuilderDialogInner({
  mode,
  sourceSkillId,
  initialRequest,
  onClose,
  onCreated,
}: {
  readonly mode: SkillBuilderMode
  readonly sourceSkillId: string | null
  readonly initialRequest: string
  readonly onClose: () => void
  readonly onCreated?: (skillId: string, options: { readonly openTab: SkillBuilderOpenTab }) => void
}) {
  const t = useTranslations('skill.builderDialog')
  const start = useStartSkillBuilder()
  const confirm = useConfirmSkillBuilderSession()
  const [request, setRequest] = useState(initialRequest)
  const [session, setSession] = useState<SkillBuilderSession | null>(null)
  const [systemLlmMissing, setSystemLlmMissing] = useState(false)
  const [sourceConflict, setSourceConflict] = useState(false)

  const draft = session?.draft_package ?? null
  const canStart = request.trim().length > 0 && !start.isPending
  const canConfirm = session?.status === 'review' && !confirm.isPending && !sourceConflict

  async function startBuilderSession(trimmed: string) {
    setSystemLlmMissing(false)
    setSourceConflict(false)
    try {
      const nextSession = await start.mutateAsync(
        mode === 'improve'
          ? { mode, source_skill_id: sourceSkillId, user_request: trimmed }
          : { mode, user_request: trimmed },
      )
      setSession(nextSession)
    } catch (err) {
      if (err instanceof ApiError && err.code === 'SYSTEM_LLM_NOT_CONFIGURED') {
        setSystemLlmMissing(true)
        return
      }
      toast.error(err instanceof Error ? err.message : t('startFailed'))
    }
  }

  async function handleStart() {
    const trimmed = request.trim()
    if (!trimmed) {
      toast.error(t('requestRequired'))
      return
    }
    await startBuilderSession(trimmed)
  }

  async function handleReloadLatest() {
    const trimmed = request.trim()
    if (!trimmed) {
      toast.error(t('requestRequired'))
      return
    }
    await startBuilderSession(trimmed)
  }

  async function handleConfirm() {
    if (!session) return
    try {
      const created = await confirm.mutateAsync(session.id)
      toast.success(mode === 'improve' ? t('toast.improved') : t('toast.created'))
      onCreated?.(created.id, { openTab: session.eval_result ? 'evaluation' : 'content' })
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'SKILL_BUILDER_SOURCE_CONFLICT') {
        setSourceConflict(true)
        return
      }
      toast.error(err instanceof Error ? err.message : t('confirmFailed'))
    }
  }

  return (
    <>
      <DialogShell.Header
        icon={<DomainIconTile iconId="skill" className="size-9" iconClassName="size-5" />}
        title={mode === 'improve' ? t('titleImprove') : t('titleCreate')}
        description={mode === 'improve' ? t('descriptionImprove') : t('descriptionCreate')}
      />
      <DialogShell.Body className="grid min-h-0 gap-4 lg:grid-cols-2">
        <section className="moldy-panel flex min-h-0 flex-col overflow-hidden">
          <div className="moldy-panel-header flex items-center gap-2 px-4 py-3">
            <MessageSquareText className="size-4 text-primary-strong" />
            <h3 className="text-sm font-semibold">{t('chatTitle')}</h3>
          </div>
          <div className="flex flex-1 flex-col gap-4 p-4">
            <label htmlFor="skill-builder-request">{t('requestLabel')}</label>
            <Textarea
              id="skill-builder-request"
              value={request}
              rows={7}
              placeholder={t(
                mode === 'improve' ? 'requestImprovePlaceholder' : 'requestPlaceholder',
              )}
              onChange={(event) => setRequest(event.target.value)}
            />
            {systemLlmMissing ? <SystemLlmReadiness /> : null}
            {sourceConflict ? (
              <ImprovementConflict
                reloadPending={start.isPending}
                onReloadLatest={handleReloadLatest}
                onDiscard={onClose}
              />
            ) : null}
            <div className="mt-auto flex justify-end">
              <Button type="button" onClick={handleStart} disabled={!canStart}>
                {start.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                {mode === 'improve' ? t('startImprove') : t('startCreate')}
              </Button>
            </div>
          </div>
        </section>

        <section className="moldy-panel flex min-h-0 flex-col overflow-hidden">
          <div className="moldy-panel-header flex items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-2">
              <FileCode2 className="size-4 text-primary-strong" />
              <h3 className="text-sm font-semibold">{t('previewTitle')}</h3>
            </div>
            {session ? (
              <span className="moldy-status-surface moldy-status-info rounded-md px-2 py-0.5 moldy-ui-micro font-medium">
                {t(`status.${session.status}`)}
              </span>
            ) : null}
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
            <SkillBuilderPreview session={session} draft={draft} />
          </div>
        </section>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button type="button" variant="outline" onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button type="button" onClick={handleConfirm} disabled={!canConfirm}>
          {confirm.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <CheckCircle2 className="size-4" />
          )}
          {mode === 'improve' ? t('confirmImprove') : t('confirmCreate')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}

function ImprovementConflict({
  reloadPending,
  onReloadLatest,
  onDiscard,
}: {
  readonly reloadPending: boolean
  readonly onReloadLatest: () => void
  readonly onDiscard: () => void
}) {
  const t = useTranslations('skill.builderDialog.conflict')
  return (
    <div className="moldy-status-surface moldy-status-warn flex flex-col gap-3 rounded-md p-3">
      <div className="flex items-start gap-3">
        <AlertTriangle className="moldy-status-icon mt-0.5 size-4 shrink-0" />
        <div>
          <p className="moldy-status-text text-sm font-semibold">{t('title')}</p>
          <p className="moldy-status-muted-text mt-1 text-xs">{t('description')}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={onReloadLatest} disabled={reloadPending}>
          {reloadPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {t('reloadLatest')}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onDiscard}>
          {t('discard')}
        </Button>
      </div>
    </div>
  )
}

function SystemLlmReadiness() {
  const t = useTranslations('skill.builderDialog.systemLlm')
  return (
    <div className="moldy-status-surface moldy-status-warn flex items-start gap-3 rounded-md p-3">
      <AlertTriangle className="moldy-status-icon mt-0.5 size-4 shrink-0" />
      <div>
        <p className="moldy-status-text text-sm font-semibold">{t('title')}</p>
        <p className="moldy-status-muted-text mt-1 text-xs">{t('description')}</p>
      </div>
    </div>
  )
}
