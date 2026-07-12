'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useAtomValue } from 'jotai'
import { ArrowLeftIcon, CheckCircle2Icon, FileTextIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import {
  parseTargets,
  targetLabel,
  type TargetStatus,
} from '@/components/skill/portable-compatibility-panel'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillBuilderFileContent, useSkillBuilderFiles } from '@/lib/hooks/use-skill-builder'
import {
  chatSkillDraftBriefAtom,
  chatSkillValidationAtom,
  type SkillDraftBrief,
} from '@/lib/stores/chat-skill-builder'
import type { SkillBuilderSession } from '@/lib/types'
import { cn } from '@/lib/utils'
import { formatDisplayBytes } from '@/lib/utils/display-format'
import {
  deriveHeadState,
  deriveStatusRows,
  hasEvals,
  hasScripts,
  mergeRailFiles,
  type HeadState,
  type RailFileEntry,
  type StatusTone,
} from './skill-builder-rail-model'

export type RailMode = 'status' | 'source'

/**
 * 스킬 빌더 챗 우측 레일 (M7 — skill-studio 목업 차용).
 *
 * 상태 모드: 목업의 "상태" 카드 — 통과/주의/오류 pill, 검증 행(실제 검증기
 * 이슈 코드 매핑), 런타임 호환 칩(compatibility_result.targets), Credential/
 * 샌드박스/평가 행. 소스 모드: 드래프트 파일 목록 + 읽기 전용 뷰어 — 목업의
 * 소스 탭을 Phase 1로 각색(저장 전 드래프트는 세션 파일 API로만 조회 가능).
 */
export function SkillBuilderRail({
  conversationId,
  session,
  mode,
  onModeChange,
}: {
  readonly conversationId: string
  readonly session: SkillBuilderSession
  readonly mode: RailMode
  readonly onModeChange: (mode: RailMode) => void
}) {
  const briefByConversation = useAtomValue(chatSkillDraftBriefAtom)
  const validationByConversation = useAtomValue(chatSkillValidationAtom)
  const brief = briefByConversation[conversationId]
  const liveValidation = validationByConversation[conversationId]

  // 라이브 이벤트(런마다 갱신)가 세션 스냅샷보다 최신 — 있으면 우선.
  const validation = liveValidation?.validation_result ?? session.validation_result ?? null
  const liveCompatibility = liveValidation?.validation_result.compatibility_result
  const compatibility =
    typeof liveCompatibility === 'object' && liveCompatibility !== null
      ? liveCompatibility
      : (session.compatibility_result ?? null)

  const { data: filesData } = useSkillBuilderFiles(session.id)
  const files = mergeRailFiles(brief, filesData?.files)

  return (
    <aside
      className="moldy-panel hidden w-96 min-w-0 shrink-0 flex-col gap-3 overflow-y-auto p-4 md:flex"
      data-testid="skill-builder-rail"
    >
      {mode === 'source' ? (
        <SourcePane
          key={session.id}
          sessionId={session.id}
          files={files}
          onBack={() => onModeChange('status')}
        />
      ) : (
        <StatusPane
          session={session}
          brief={brief}
          validation={validation}
          compatibility={compatibility}
          files={files}
          onOpenSource={() => onModeChange('source')}
        />
      )}
    </aside>
  )
}

// ── 상태 모드 (목업 "상태" 카드) ─────────────────────────────────────────

function StatusPane({
  session,
  brief,
  validation,
  compatibility,
  files,
  onOpenSource,
}: {
  readonly session: SkillBuilderSession
  readonly brief: SkillDraftBrief | undefined
  readonly validation: unknown
  readonly compatibility: unknown
  readonly files: readonly RailFileEntry[]
  readonly onOpenSource: () => void
}) {
  const t = useTranslations('skill.builderChat')
  const head = deriveHeadState(validation)
  const rows = deriveStatusRows(validation)
  const targets = parseTargets(compatibility as never)
  const credentialCount = brief?.credential_requirement_count ?? 0

  return (
    <>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">{t('railTitle')}</h2>
          <p className="mt-0.5 text-xs font-medium text-muted-foreground">
            {session.mode !== 'improve'
              ? t('railSubtitleCreate')
              : session.base_skill_version
                ? t('railSubtitleImprove', { version: session.base_skill_version })
                : t('railSubtitleImproveNoVersion')}
          </p>
        </div>
        <HeadPill head={head} />
      </div>

      {session.finalized_skill_id ? (
        <div
          className="moldy-muted-panel flex items-center gap-2 p-3"
          data-testid="builder-completed-banner"
        >
          <CheckCircle2Icon className="moldy-status-icon size-4" />
          <span className="flex-1 text-sm">{t('completedTitle')}</span>
          <Link
            href={`/skills/${session.finalized_skill_id}/source`}
            className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
          >
            {t('openSkill')}
          </Link>
        </div>
      ) : null}

      <section className="space-y-2" data-testid="builder-status-rows">
        {rows.map((row) => (
          <div
            key={row.key}
            title={row.detail ?? undefined}
            className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-background px-3 py-2 text-xs"
          >
            <span className="text-foreground/80">
              {t(`statusRow.${row.key}`, { count: row.count ?? 0 })}
            </span>
            <ToneStatus tone={row.tone} />
          </div>
        ))}

        {/* 런타임 호환 — 목업 rt-chips (실데이터: compatibility_result.targets) */}
        <div className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-background px-3 py-2 text-xs">
          <span className="shrink-0 break-keep text-foreground/80">
            {t('statusRow.runtimeCompat')}
          </span>
          {targets.length > 0 ? (
            <span className="flex flex-wrap justify-end gap-1" data-testid="builder-runtime-chips">
              {targets.map((target) => (
                <RuntimeChip key={target.key} targetKey={target.key} status={target.status} />
              ))}
            </span>
          ) : (
            <PendingLabel />
          )}
        </div>

        <MetaRow label={t('statusRow.credentials')}>
          {credentialCount > 0
            ? t('statusValue.credentialCount', { count: credentialCount })
            : t('statusValue.none')}
        </MetaRow>
        <MetaRow label={t('statusRow.sandbox')}>
          {hasScripts(files) ? t('statusValue.scriptsPresent') : t('statusValue.noScripts')}
        </MetaRow>
        <MetaRow label={t('statusRow.evaluation')}>
          {hasEvals(files) ? t('statusValue.evalsReady') : t('statusValue.evalsPending')}
        </MetaRow>
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">{t('filesTitle')}</h3>
          {brief && brief.changed_count > 0 ? (
            <Badge variant="secondary" className="ml-auto">
              {t('changedCount', { count: brief.changed_count })}
            </Badge>
          ) : null}
        </div>
        {files.length > 0 ? (
          <ul className="space-y-0.5" data-testid="builder-draft-files">
            {files.map((file) => (
              <li key={file.path}>
                <button
                  type="button"
                  onClick={onOpenSource}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                >
                  <FileTextIcon className="size-3 shrink-0" />
                  <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
                  <span className="shrink-0 text-muted-foreground/70">
                    {formatDisplayBytes(file.size)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">{t('filesEmpty')}</p>
        )}
      </section>
    </>
  )
}

function MetaRow({
  label,
  children,
}: {
  readonly label: string
  readonly children: React.ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-background px-3 py-2 text-xs">
      <span className="text-foreground/80">{label}</span>
      <Badge variant="outline">{children}</Badge>
    </div>
  )
}

function HeadPill({ head }: { readonly head: HeadState }) {
  const t = useTranslations('skill.builderChat')
  switch (head.tone) {
    case 'pass':
      return (
        <Badge variant="secondary" className="bg-status-success/15 text-status-success">
          {t('headPill.pass')}
        </Badge>
      )
    case 'warn':
      return (
        <Badge variant="secondary" className="bg-status-warn/15 text-status-warn">
          {t('headPill.warn', { count: head.warningCount })}
        </Badge>
      )
    case 'error':
      return (
        <Badge variant="secondary" className="bg-status-danger/15 text-status-danger">
          {t('headPill.error', { count: head.errorCount })}
        </Badge>
      )
    default:
      return <Badge variant="outline">{t('headPill.pending')}</Badge>
  }
}

function ToneStatus({ tone }: { readonly tone: StatusTone }) {
  const t = useTranslations('skill.builderChat')
  if (tone === 'pending' || tone === 'none') return <PendingLabel />
  const label =
    tone === 'error'
      ? t('tone.error')
      : tone === 'warn'
        ? t('tone.warn')
        : tone === 'good'
          ? t('tone.good')
          : t('tone.pass')
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 font-medium',
        tone === 'error' && 'text-status-danger',
        tone === 'warn' && 'text-status-warn',
        (tone === 'pass' || tone === 'good') && 'text-status-success',
      )}
    >
      <span
        className={cn(
          'inline-block size-1.5 rounded-full',
          tone === 'error' && 'bg-status-danger',
          tone === 'warn' && 'bg-status-warn',
          (tone === 'pass' || tone === 'good') && 'bg-status-success',
        )}
      />
      {label}
    </span>
  )
}

function PendingLabel() {
  const t = useTranslations('skill.builderChat')
  return <span className="text-muted-foreground/70">{t('tone.pending')}</span>
}

function RuntimeChip({
  targetKey,
  status,
}: {
  readonly targetKey: string
  readonly status: TargetStatus
}) {
  const t = useTranslations('skill.compatibility')
  return (
    <span
      className={cn(
        'rounded-md border px-1.5 py-0.5 text-xs font-semibold',
        status === 'pass' && 'border-status-success/40 bg-status-success/10 text-status-success',
        (status === 'warning' || status === 'unknown') &&
          'border-status-warn/40 bg-status-warn/10 text-status-warn',
        status === 'error' && 'border-status-danger/40 bg-status-danger/10 text-status-danger',
      )}
    >
      {targetLabel(targetKey, t)}
    </span>
  )
}

// ── 소스 모드 (목업 소스 탭의 Phase 1 각색) ─────────────────────────────

function SourcePane({
  sessionId,
  files,
  onBack,
}: {
  readonly sessionId: string
  readonly files: readonly RailFileEntry[]
  readonly onBack: () => void
}) {
  const t = useTranslations('skill.builderChat')
  const [selectedPath, setSelectedPath] = useState<string | null>(files[0]?.path ?? null)
  const effectivePath = selectedPath ?? files[0]?.path ?? null
  const { data: content, isLoading } = useSkillBuilderFileContent(sessionId, effectivePath)

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="builder-source-pane">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          aria-label={t('sourceBack')}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 hover:text-foreground"
        >
          <ArrowLeftIcon className="size-4" />
        </button>
        <h2 className="text-sm font-semibold">{t('sourceTitle')}</h2>
      </div>

      {files.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('filesEmpty')}</p>
      ) : (
        <>
          <ul className="max-h-40 shrink-0 space-y-0.5 overflow-y-auto rounded-lg border border-border/70 p-1.5">
            {files.map((file) => (
              <li key={file.path}>
                <button
                  type="button"
                  onClick={() => setSelectedPath(file.path)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-xs',
                    effectivePath === file.path
                      ? 'bg-primary/60 font-semibold text-foreground'
                      : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                  )}
                >
                  <FileTextIcon className="size-3 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{file.path}</span>
                  <span className="shrink-0 text-muted-foreground/70">
                    {formatDisplayBytes(file.size)}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border/70">
            <div className="flex items-center gap-2 border-b border-border/60 bg-muted/40 px-3 py-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs font-semibold">
                {effectivePath ?? '—'}
              </span>
              {content?.role ? <Badge variant="outline">{content.role}</Badge> : null}
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3" data-testid="builder-source-viewer">
              {isLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-4/5" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              ) : (
                <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-foreground/85">
                  {content?.content ?? ''}
                </pre>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
