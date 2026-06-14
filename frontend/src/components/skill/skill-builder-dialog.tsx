'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { CheckCircle2, FileCode2, Loader2, MessageSquareText, Sparkles } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile } from '@/components/shared/icon'
import { skillBuilderApi } from '@/lib/api/skill-builder'
import { ApiError } from '@/lib/api/client'
import { useSession } from '@/lib/auth/session'
import { useConfirmSkillBuilderSession, useStartSkillBuilder } from '@/lib/hooks/use-skill-builder'
import { streamSkillBuilderMessage } from '@/lib/sse/stream-skill-builder-message'
import { SkillBuilderPreview } from './skill-builder-preview'
import { ImprovementConflict, SystemLlmReadiness } from './skill-builder-status-panels'
import type { SkillBuilderMode, SkillBuilderSession, SkillBuilderStreamEvent } from '@/lib/types'

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
  const { data: user } = useSession()
  const [request, setRequest] = useState(initialRequest)
  const [session, setSession] = useState<SkillBuilderSession | null>(null)
  const [systemLlmMissing, setSystemLlmMissing] = useState(false)
  const [sourceConflict, setSourceConflict] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)

  const draft = session?.draft_package ?? null
  const pending = start.isPending || isStreaming
  const canStart = request.trim().length > 0 && !pending
  const canConfirm =
    session?.status === 'review' && !confirm.isPending && !sourceConflict && !isStreaming

  async function startBuilderSession(trimmed: string) {
    setSystemLlmMissing(false)
    setSourceConflict(false)
    setIsStreaming(false)
    try {
      const nextSession = await start.mutateAsync(
        mode === 'improve'
          ? { mode, source_skill_id: sourceSkillId, user_request: trimmed }
          : { mode, user_request: trimmed },
      )
      setSession(nextSession)
      setIsStreaming(true)
      for await (const event of streamSkillBuilderMessage(nextSession.id, { content: trimmed })) {
        handleSkillBuilderStreamEvent(event)
      }
      setSession(await skillBuilderApi.get(nextSession.id))
    } catch (err) {
      if (err instanceof ApiError && err.code === 'SYSTEM_LLM_NOT_CONFIGURED') {
        setSystemLlmMissing(true)
        return
      }
      if (err instanceof SkillBuilderStreamEventError) {
        toast.error(err.streamMessage ?? t('startFailed'))
        return
      }
      toast.error(err instanceof Error ? err.message : t('startFailed'))
    } finally {
      setIsStreaming(false)
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
            {systemLlmMissing ? (
              <SystemLlmReadiness isSuperUser={Boolean(user?.is_super_user)} />
            ) : null}
            {sourceConflict ? (
              <ImprovementConflict
                reloadPending={start.isPending}
                onReloadLatest={handleReloadLatest}
                onDiscard={onClose}
              />
            ) : null}
            <div className="mt-auto flex justify-end">
              <Button type="button" onClick={handleStart} disabled={!canStart}>
                {pending ? (
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

function handleSkillBuilderStreamEvent(event: SkillBuilderStreamEvent): void {
  switch (event.event) {
    case 'message_start':
    case 'builder_status':
    case 'builder_activity':
    case 'draft_package':
    case 'validation_result':
    case 'compatibility_result':
    case 'changelog_draft':
    case 'eval_result':
    case 'content_delta':
    case 'message_end':
      return
    case 'error':
      throw new SkillBuilderStreamEventError(streamMessage(event.data.message))
    default:
      assertNever(event)
  }
}

class SkillBuilderStreamEventError extends Error {
  constructor(readonly streamMessage: string | null) {
    super('skill_builder_stream_error')
  }
}

function streamMessage(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function assertNever(value: never): never {
  throw new Error(`Unexpected Skill Builder event: ${JSON.stringify(value)}`)
}
